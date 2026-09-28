# Magi / Firstmate structured execution bridge v1

## Boundary

This additive bridge implements objective submission, product-safe Fleet,
Activity, cancellation requests, and verified completion after Native Chat
Phase 1. In restricted mode Firstmate remains the local scheduler/worker owner;
in public-SaaS mode the provider-neutral isolation backend owns ephemeral
worker lifecycle under the durable hosted controller.

- **Chat** offers one closed `firstmate.submit_objective` tool only for
  actionable work; ordinary conversation remains provider-native Chat.
- **Firstmate intake** receives one deterministic queued task plus a bounded
  native inbox wake in restricted mode. Hosted mode instead claims the same
  accepted objective ledger and idempotently requests isolated capacity; see
  [`hosted-execution.md`](./hosted-execution.md).
- **Fleet and Activity** read persisted submission, execution-event, decision,
  and cancellation rows only. They never probe a process or terminal.
- A new Chat row is generated only after a typed `objective.completed` event
  carries closed, bounded verification evidence.

The implementation owners are:

- `gateway/app/firstmate_execution.py` — strict event/evidence contracts,
  principal-scoped correlation and idempotency, Activity projection, completion
  wake/recovery;
- `gateway/app/firstmate_execution_api.py` — authenticated producer/read/retry
  routes;
- `gateway/app/activity_store.py` — canonical Activity projection/replay;
- `gateway/app/magi_chat_{service,store}.py` — generic verified-outcome model
  input and the accepted Native Chat persistence/replay path.

## Event contract

`firstmate.execution-event.v1` has one immutable `event_id` and exact
`objective_id`, `task_id`, and `run_id` causality. The accepted event additionally
binds the objective to an existing owner-scoped native conversation and user
message. Later events must match that binding. Reusing an event id with changed
facts, correlating one task to another objective, changing a run, or writing
past a terminal event is a conflict.

The closed phase vocabulary is:

1. `objective.accepted`
2. `worker.started`
3. `implementation.started`
4. `tests.started`
5. `tests.passed` or `tests.failed`
6. `review.started`
7. `objective.completed`, `objective.failed`, or `objective.cancelled`

Milestones are immutable Activity records with stable source-event identities.
Activity exposes their typed `objective.accepted`, `worker.started`,
`implementation.started`, `tests.started`, `tests.passed`, `tests.failed`, and
`review.started` kinds plus the existing terminal objective kinds, using fixed
Gateway-authored summaries. No producer summary, status line, worker response,
hook payload, command, tool output, terminal bytes, ANSI, or Herdr data has a
contract field. Historical active milestones stop contributing to Activity
focus/active counts once a structured terminal objective fact exists.

The authenticated producer route is:

```text
POST /api/v1/firstmate/execution-events
```

It accepts a `response`- or `command`-scoped principal. Ownership always comes
from that bearer session; an owner field is forbidden. Reads are
`GET /api/v1/firstmate/execution-events/{event_id}` with `read` scope. These
routes are available only while Native Chat is enabled.

## Durable objective intake

`firstmate.submit_objective` accepts a strict, bounded objective contract and
derives owner, conversation, message, objective, and task identity outside model
output. `tasks-axi add` publishes the deterministic task to Firstmate's durable
queue. After validating the exact structured receipt, Gateway invokes
`fm-inbox.sh note`; that script persists the note and Firstmate's normal `check`
wake. It is a doorbell, not a second scheduler. Capacity, dependencies,
classification, worker creation, and queued-work reevaluation remain Firstmate's
responsibility.

A restricted-mode submission stays `submitting` until queue publication and
the wake succeed. A process crash or wake failure therefore leaves a retryable
persisted row. One bounded startup recovery pass replays `tasks-axi add` and the
wake; the task's deterministic identity and a dispatch lease make replay and
concurrent client retries idempotent. Hosted mode marks that same durable row
accepted without touching a shared Firstmate home; its write-side controller
uses expiring launch leases, deterministic external execution identity, and
continuous queued-work recovery. Neither mode performs execution work from a
product read path.

## Product Fleet and cancellation

`GET /api/v1/fleet` returns stable concise titles plus structured goal, status,
activity, worker presence, allowlisted artifacts, decisions, and cancellation
state. The product projection omits task IDs, run IDs, panes, PIDs, terminal
controls, transcripts, and raw worker output. Internal compatibility projections
remain separate.

`POST /api/v1/fleet/objectives/{objective_id}/cancellation-requests` requires a
`command` principal, explicit UI confirmation, and an idempotency key. It
persists the request before handing a keyed note to Firstmate. The response says
only `requested`; cancellation becomes `observed` solely when an authenticated
`objective.cancelled` event arrives. Failed/pending note delivery is retried by
the same key and by the bounded startup recovery pass. A different terminal
event marks the request not applied instead of claiming cancellation.

Model-generated arguments must not be exposed to the event seam as evidence:
only facts accepted by the trusted Firstmate adapter belong there.

## Verified completion

`objective.completed` is rejected unless it carries
`firstmate.completion-evidence.v1` with:

- literal `result: completed` and `verification: verified`;
- at least one and at most 32 uniquely identified checks;
- every check typed as acceptance, test, typecheck, lint, build, review, or
  deployment and explicitly `passed`;
- at most 16 typed artifact references (canonical forge PR/MR URL, bounded
  report id, or hexadecimal commit id), of which at most eight are public
  Activity references.

Labels and identities are bounded inert text and credential-shaped content is
rejected. The canonical evidence JSON and SHA-256 binding are persisted before
model work begins.

After that transaction commits, `FirstmateExecutionService` wakes
`MagiChatService.generate_verified_outcome`. Its model call contains no prior
chat history and no execution transcript. The only variable input is canonical
JSON built from the accepted objective title/project, completion time, passed
checks, and allowlisted artifacts. The fixed instruction asks for a concise
user-facing report and forbids invention or infrastructure language.

The result is reserved as a **new assistant-only** `magi_messages` row in the
same owner-scoped native conversation. Its reply edge points to the original
user message, but no synthetic user message is created. It uses the same
non-streamed model-result validation, exact final-byte persistence,
`magi_message_changes` replay, HTTP reads, and `magi_messages` WebSocket
transport as physical-iPhone Native Chat.

A source event and generated message each have independent idempotency ledgers.
Concurrent/duplicate completion delivery invokes the provider once and replays
the same assistant id. Provider failure leaves that canonical assistant row
truthfully failed and does not create completion prose. Retrying requires the
explicit bounded wake contract:

```text
POST /api/v1/firstmate/execution-events/{event_id}/wake
{"retry_failed": true}
```

The retry reuses and revises the same assistant row. On Gateway startup, native
pending rows are first marked with the existing truthful `server_restart`
failure, then interrupted completion claims are requeued from persisted
evidence and woken. A completed native row discovered after a crash is adopted
without a second provider call.

## Live acceptance

Use [`magi-execution-live-acceptance.md`](magi-execution-live-acceptance.md) for
the opt-in real-provider, real-dispatcher, and real-worker verification. Its
committed evidence is deliberately sanitized; run-specific secrets, identities,
and raw captures stay outside the repository.

## Non-claims

This bridge does not prove that Firstmate performed work merely because a model
asked it to or a queued task exists. It accepts completion only at the structured
producer boundary and does not infer phases from prose, pane state, PR text, or
a harness stop. It does not implement streaming, deployment, real-provider
console registration, or physical-device acceptance.
