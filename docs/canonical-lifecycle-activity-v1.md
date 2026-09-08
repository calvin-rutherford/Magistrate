# Canonical lifecycle and activity v1

Status: additive SOAK P0 contract. `conversation.v1` and `magi.event.v1` remain the captain-chat authority.

## Boundaries and identities

A submitted captain prompt is still one canonical conversation turn. The turn now carries two separate causal identities:

- `turn_id`: conversation ordering and attribution;
- `objective_id`: the durable objective accepted from the captain;
- `run_id`: this execution/incarnation of that objective.

The values are never aliases. New turns receive opaque `ct_`, `obj_`, and `run_` values. Migration gives existing owner rows deterministic `obj_legacy_…` and `run_legacy_…` values without replacing turn/message ids or text.

A turn reserves its original `assistant_message_id` as ordinal zero (`kind=response`). An authenticated producer may reserve bounded additional `progress`, `decision`, or `outcome` messages with an idempotency key. Their fixed slots sort after prompt/tool rows and before the original final response. Each message has an independent, gap-free `magi.event.v1` revision stream. The first event remains `assistant.started`; duplicate event id plus identical payload is a no-op, while identity/content reuse is a conflict.

Any accepted semantic event owns the whole objective. From that point, synchronous or terminal-derived fallback cannot race into any assistant slot. Additional progress completion leaves the objective active. A primary response or outcome can complete it; explicit failed/cancelled events are terminal. `assistant.awaiting_user` requires a stable decision key. The lifecycle states are:

`active | awaiting-user | completed | failed | cancelled`

Lifecycle revisions are monotonic and travel on every conversation row so an unchanged message can still deliver a state transition.

## Tenant ownership

Every database read/write is qualified with the `user_id` from the authenticated Gateway `Principal`. Activity, turn, reservation, replay, diagnostics, HTTP, and WebSocket contracts accept no client-supplied owner id. A source event observed for two configured tenants produces two tenant-owned canonical rows with different canonical ids.

The frontend establishes the server-validated principal before protected routes mount. Captain canonical and pending cache keys use the unambiguous `<encoded-principal>|<encoded-target>` scope and their payload repeats `principal_id`; mismatches fail closed. Logout, expiry, a protected 401, or a server-observed principal change synchronously clears in-memory chat and removes the old principal's persisted keys after outstanding writes settle. Unqualified historical caches are deleted, not migrated.

## Firstmate structured adapter

`gateway/app/firstmate_activity.py` is the only source adapter. It does not import Herdr, inspect panes, read terminal output, parse ANSI, or infer an audience from prose. It consumes three semantic sources:

1. `fm-fleet-snapshot.sh --json`: current structured task/completion and exact keyed-decision facts. Runtime state is accepted only with allowlisted semantic provenance. Missing, `pane`, terminal, and unknown provenance fail closed.
2. `state/branch-outcomes.jsonl`: append-only supervision outcomes. The independent Magistrate cursor never reads or writes Pi/Firstmate cursor sidecars. `wake` and optional endpoint provenance are hashed but never exported.
3. `state/captain-events/events.jsonl`: the explicitly enabled private append-only `fm-captain-event.v1` outbox produced at Pi's persisted `turn_end` semantic boundary.

The Gateway never enables or reloads the producer; Firstmate's upgrade contract requires the operator to reload the applicable Pi sessions before writing the exact home-local activation flag. The production consumer uses Firstmate's versioned `fm-captain-event.sh enabled/read/ack` tooling and independently requires canonical strict UTF-8/JSON with no duplicate keys, an owned single-link mode-0600 regular journal, a final newline, at most 10,000 rows / 81,920,000 bytes, and at most 8 KiB including each event newline. The configured Firstmate home has one gap-free sequence from one, and every row must match that adapter's explicit `source_home` binding (`main` or `secondmate:<stable-id>`); journals are never copied or mixed across homes. This slice instantiates the main-home binding, while a future independently enabled secondmate home requires its own adapter/source instance. Required semantics are:

```json
{
  "schema": "fm-captain-event.v1",
  "seq": 1,
  "event_id": "sha256:<identity-digest>",
  "published_at_ms": 1788840000000,
  "occurred_at_ms": null,
  "source_home": "secondmate:ios",
  "source_role": "worker",
  "task_id": "soak-worker",
  "incarnation": "s1788837905.2016558.509",
  "producer": "pi",
  "harness_event_id": "persisted-session-entry-id",
  "audience": "captain",
  "kind": "worker.message",
  "summary": "inert text, at most 600 codepoints",
  "summary_truncated": false,
  "refs": {"report_id": "soak-report"}
}
```

The field set is exact, with no aliases. Source role and kind must agree: `primary.message|primary.final` has no task id; `worker.message|worker.final` requires one. The event id is recomputed from schema, source home/role, task, incarnation, producer, and harness-event identity. Unknown schema, kind, audience, field, reference, source identity, non-canonical JSON, sequence, duplicate id, or identity digest stops the source before cursor advance. Prompt, terminal, output, tool, reasoning, environment, credentials, arbitrary paths, transcript, and ANSI have no schema path. Firstmate applies bounded high-confidence credential-shape redaction (not a mathematical guarantee over arbitrary prose); the consumer independently rejects the same deterministic residual forms. Source refs are the exact optional object keys `pr_url`, `report_id`, safe `report_path`, and positive `branch_outcome_seq`; clients receive only the existing canonical GitHub PR / GitLab MR URL grammars and bounded report ids.

Canonical activity preserves the four source message kinds and `summary_truncated` truth directly. Their record state is `completed` because publication happens only after Pi persisted that assistant turn; `primary.final` / `worker.final` describe Pi's per-turn `stopReason=stop` and **never** assert that a Firstmate task or objective completed. Task lifecycle remains owned by validated fleet/branch facts.

The adapter normalizes source-native identity into `(source_instance, stream, event_id, source_sequence, payload_hash)`. It writes the immutable source event, canonical activity projection, activity change-ledger row, and new source cursor in one SQLite transaction. Only after commit does it call Firstmate `ack` with that exact sequence/event id; before a later read it may reassert only that already-durable pair so Firstmate can finish an interrupted atomic acknowledgement. Parsed-but-uncommitted input is never acknowledged. Stable objective identity derives from the bound source instance plus task/primary role; run identity additionally includes incarnation. Neither borrows an unrelated conversation turn.

## Bootstrap, restart, and conflict policy

Bootstrap is explicit per source:

- fleet snapshot: `snapshot-current`;
- branch outcomes: `tail` by default, so a first deployment validates/checkpoints existing history without injecting it (`MAGISTRATE_FIRSTMATE_ACTIVITY_BOOTSTRAP=from-start|tail|after:N`);
- captain semantic outbox: this slice explicitly chooses `from-start` by default because it is bounded, explicitly captain-addressed, and is the delivery ledger (`MAGISTRATE_FIRSTMATE_CAPTAIN_BOOTSTRAP=from-start|tail|after:N`). Operators choosing Firstmate's usual checkpoint-current-tail posture set `tail`; any nonzero checkpoint is durably registered before its exact Firstmate acknowledgement.

Each append-only stream persists its cursor and an exact validated line-prefix SHA-256. Byte rewrites (including whitespace/separators), truncation, cursor-ahead, malformed/gapped/reordered rows, or event-id payload conflict mark the source faulted while preserving accepted rows and cursor. Snapshot timestamps cannot regress; reusing a timestamp with changed semantic content is a conflict. A disappearing keyed decision means only `decision.resolved`, never approved/rejected or objective completion.

Gateway startup launches a bounded rotating cadence across known conversation/activity/active-session principals, so large tenant sets are covered without inventing an owner or making one unbounded pass. This is restart recovery, not producer ownership. The outbox remains authoritative and retryable while Gateway is down.

## Delivery and observability

Authenticated, principal-scoped endpoints:

- `GET /api/v1/conversations/{target}/replay?after=…`
- `GET /api/v1/conversations/{target}/turns/{turn_id}`
- `POST /api/v1/conversations/{target}/turns/{turn_id}/assistant-messages`
- `GET /api/v1/activity` (optional source reconciliation plus replay)
- `GET /api/v1/activity/snapshot` (bounded current projection, active/decision focus, summary, and replay cursor)
- `GET /api/v1/activity/replay` (durable SQLite replay only; no source or terminal I/O)
- `POST /api/v1/activity/catch-up`
- `GET /api/v1/diagnostics/soak`

Conversation `replay?after=` pages the tenant-local, append-only `conversation_changes.change_sequence` ledger, not render-order `sequence_index` and not history pagination. Every visible message insertion or revision commits a new change identity; replay returns the current projection for each ledger entry. Thus a progress message committed later at render index 900 is delivered after an already observed primary response at render index 999, and duplicate transports still reconcile by stable message id and revision. The cursor survives restart, begins at `-1`, and a cursor ahead of that principal's ledger fails closed. Authoritative HTTP refreshes replace the bounded conversation window, and every new captain WebSocket connection starts with that current bounded window before tracking `(message revision, turn status, lifecycle revision)` deltas; therefore an in-place revision at an already observed message sequence is replayed after reconnect.

The existing WebSocket remains backward compatible. Activity is sent only when the authenticated control frame opts in with non-negative `activity_after`; the chat delivery loop reads durable rows only and never waits on Firstmate. Activity record `sequence` is stable insertion order, while `delivery_sequence` is an append-only change cursor, so in-place decision/lifecycle revisions are replayable after disconnect. Each change entry carries the record's current projection (an older delivery entry can therefore repeat a causally identical newer revision), and `activity_records` carries the same id/revision/change rows as HTTP replay.

`CanonicalActivity.ts` strictly validates and merges snapshot, HTTP, and socket rows by stable id/revision. A delayed snapshot cannot replace a newer revision or summary; a complete focus projection can remove a stale cached non-terminal fact without inventing its outcome. Its bounded cache repeats the authenticated principal and is cleared synchronously in memory before an account change can paint. Foreground/reconnect and pull-to-refresh recover from the persisted cursor. The native activity sheet pages bounded records, exposes exact keyed decisions through their existing Attention item identity, and labels source/network interruption without interpreting it as completion.

Diagnostics contain bounded counters, classifications, cursors, lag, revisions, and source health. They never retain prompts, assistant prose, raw source payloads, terminal bytes, tool I/O, or exception detail that could contain paths/credentials. Unknown terminal truncation remains `null`, not a fabricated healthy `false`.

## Explicit non-claims

This contract does not replace canonical chat, make Herdr lifecycle changes, create a complete worker transcript, or add a broker. It does not prove notification timeliness, background execution, app-store readiness, or physical-iPhone acceptance; those remain release/soak gates.
