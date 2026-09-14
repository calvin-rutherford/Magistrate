# Magi ↔ Firstmate decisions v1

Track C adds an isolated, owner-scoped path from structured Firstmate captain
holds to Attention and the closed Magi tool registry. It does **not** turn
terminal text, Herdr output, or model-authored tool arguments into a Firstmate
answer.

## Boundaries

`gateway/app/firstmate_decisions.py` is the authority for this contract.

- Input is an authenticated, strict, complete
  `firstmate.decision-events.v1` push projection at
  `POST /api/v1/firstmate/decision-events`. Each normalized
  `firstmate.decision-event.v1` carries one source-bound open lifecycle identity;
  event identity is independently recomputed before persistence. The older
  `fm-fleet-snapshot.v1` parser remains only as an explicit legacy migration
  adapter and is never called by Attention, notification, startup, or reads.
- Free-form backlog rows, status prose, agent output, terminal snapshots,
  transcripts, model output, and Herdr are not decision sources.
- The configured bootstrap owner is the sole principal for the local Firstmate
  source. Attention and Magi projections are qualified by that owner. A
  command-scoped, unexpired owner session is required to prepare or execute an
  answer.
- Generic Native Chat orchestration remains provider-independent and does not
  import Firstmate. The context, `MagiToolDefinition`, and handler below are
  isolated Track C seams; the model/tool composition owner can register them
  without making terminal or Firstmate implementation details part of chat.

## Identity and projection

One open decision is identified by the tuple:

```text
(owner_user_id, source_instance_id, Firstmate task id, hold lifecycle identity)
```

The API-facing `fmd_<digest>` is an opaque hash of that tuple. Firstmate's hold
lifecycle identity is its hold-set timestamp plus durable resolution count. A
repeated identical pushed projection keeps the same decision id and revision. A semantic
change to its title/question increments the revision. Closing or removing the
hold resolves that revision; re-holding a task creates a new lifecycle identity
and therefore a new decision id.

Ingestion records immutable, hash-checked `firstmate.decision-event.v1` source
events and rejects one source event or one observation timestamp reused for
different semantic bytes. Older snapshots cannot reopen newer state.

`GET /api/v1/attention/unified` includes these decisions as natural
`captain_question` cards. The card carries the question, opaque decision id,
and revision, but not the Firstmate task id or lifecycle identity. It has no
legacy approve/reject Attention action: its answer path is Magi Chat. Attention
reads only pending persisted rows and never refresh Firstmate or Herdr.

For model orchestration, the authenticated
`get_firstmate_decision_magi_context(principal)` seam returns
`firstmate.decision-context.v1` with bounded pending title/question data and the
opaque id/revision. The returned guidance requires treating source text as
untrusted data and selecting the answer tool only for an explicit current user
answer. Source status is the bounded timestamp/state of the last persisted push;
no read probes runtime. Execution still requires an exact persisted decision,
bound confirmation, and Firstmate's command-side lifecycle check.

## Isolated answer tool

The registry-native exported definition is `ANSWER_DECISION_DEFINITION` (with
`FIRSTMATE_ANSWER_DECISION_TOOL` retained as its equivalent wire shape):

```json
{
  "name": "firstmate.answer_decision",
  "arguments": {
    "decision_id": "fmd_…",
    "decision_revision": 1
  }
}
```

Answer text is deliberately absent. The small composition point is: append
`ANSWER_DECISION_DEFINITION` to the host-owned definitions, append
`get_firstmate_decision_magi_context(principal)` to trusted system context,
strictly decode the selected call with `parse_answer_decision_arguments`, and
invoke `handle_firstmate_answer_decision(...)` with the authenticated principal
and current canonical Native Chat **user message id** as host-owned metadata.
A principal/session and confirmation token must remain out-of-band; an
owner-id-only model context is not sufficient authority.
The handler loads exact bytes only from the owner's default captain
conversation, using a completed `magi_messages` row whose role is `user` and
whose source is `text` or `voice`. For a new answer it must also be the newest
canonical user row. It rejects missing, superseded, pending, assistant,
foreign-owner, pre-decision, malformed, oversized, control-bearing, or
credential-shaped content.

Invocation is two-step:

1. Call without a token. The service reads the owner-qualified persisted
   projection, verifies exact id/revision, and returns a short-lived confirmation
   with the consequence that the answer will be recorded and held work released.
2. After an explicit user confirmation, call with the server-held token. The
   token is bound to owner, session, decision id/revision, Native Chat message,
   answer digest, and expiry. The service revalidates the persisted revision,
   atomically claims the answer, and only then invokes the trusted Firstmate
   command, which verifies the exact open lifecycle identity.

The command receives the exact canonical bytes through a private mode-`0600`
temporary decision file and `--release`. It runs as a bounded, non-interactive
subprocess with a minimal environment. It never writes or sends the answer to a
terminal, pane, prompt, transcript, command argument, log, or HTTP payload.
The temporary file is removed after every outcome.

The Firstmate producer lock now covers `fm-captain-hold.sh` and every shell
library it sources. An explicitly configured production root must pass the
existing commit/source/artifact validation before a command can run.

## Confirmation, stale state, and retries

The SQLite state machine is:

```text
pending -> answering -> answered -> resolved
              |            (fresh source disappearance)
              +-> pending   (bounded command rejection)
              +-> resolved  (stale lifecycle)
```

- A changed revision, closed/replaced lifecycle, resolved decision, consumed or
  mismatched confirmation, foreign principal/message, or competing answer is
  rejected before a new command invocation.
- One canonical Native Chat user message is one idempotency key. It cannot be
  rebound to another decision or revision.
- An exact retry returns the durable prior outcome and does not execute again.
  A short execution lease prevents concurrent Gateway workers from invoking
  the command twice. If Gateway stopped after claiming but before recording
  completion, the same idempotency key may reclaim an expired lease. Firstmate's
  exact decision digest and release-mode replay checks make that recovery
  idempotent.
- Answer plaintext remains solely in canonical Native Chat and Firstmate's
  decision record. Magistrate decision-answer rows retain its SHA-256 digest,
  byte count, canonical message reference, actor session, status, and bounded
  evidence—never a second plaintext copy.

No outcome claims that downstream work actually ran. `succeeded` means only
that Firstmate durably accepted the exact answer and the release operation.

## Failure codes

The handler raises `FirstmateDecisionError` with a safe code and HTTP-equivalent
status for a registry adapter. Stable rejection classes include
`malformed`, `unauthorized`, `forbidden`, `decision_not_found`,
`native_message_not_found`, `native_message_stale`, `invalid_answer`, `answer_precedes_decision`,
`confirmation_required`, `confirmation_invalid`, `stale`, `already_resolved`,
`duplicate`, `idempotency_mismatch`, `unsupported_risk`, `source_invalid`,
`source_conflict`, `source_unavailable`, and `command_unavailable`.
