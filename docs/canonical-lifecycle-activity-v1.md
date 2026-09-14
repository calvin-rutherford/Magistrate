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

## Firstmate structured ingress

Normal execution Activity is push-based. The authenticated
`POST /api/v1/firstmate/execution-events` seam persists the strict
`firstmate.execution-event.v1` objective/run lifecycle described below; the
complete `POST /api/v1/firstmate/decision-events` projection persists current
captain holds for Attention. `firstmate.submit_objective` persists the original
owner-qualified assignment before dispatch. Gateway reads project those SQLite
facts and never invoke Firstmate, Herdr, a terminal parser, or a shell snapshot.
See [`gateway-runtime-observation-boundary.md`](gateway-runtime-observation-boundary.md).

`gateway/app/firstmate_activity.py` is now a retained migration/rollback adapter
only. No startup task or HTTP read route calls it, and it returns disabled when
legacy chat is off. Its historical `fm-fleet-snapshot.sh`, branch-outcome, and
`fm-captain-event.v1` readers remain to validate old stores/fixtures during an
explicit rollback; they are not current Fleet or Activity observation
mechanisms. The historical captain-event wire shape was:

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

Canonical activity preserves the four source message kinds and `summary_truncated` truth directly. Their record state is `completed` because publication happens only after Pi persisted that assistant turn; `primary.final` / `worker.final` describe Pi's per-turn `stopReason=stop` and **never** assert that a Firstmate task or objective completed. In the retained adapter, task lifecycle remains owned by its validated
legacy facts. In normal operation, task lifecycle is owned by accepted
`firstmate.execution-event.v1` records.

The retained adapter normalizes source-native identity into
`(source_instance, stream, event_id, source_sequence, payload_hash)`. Its
acknowledgement behavior applies only when that adapter is explicitly invoked
in rollback/migration operation. Normal reads never invoke it or acknowledge an
upstream cursor.

### Structured execution extension

Step 10 adds a separate authenticated push seam in
`gateway/app/firstmate_execution.py`. Its strict
`firstmate.execution-event.v1` vocabulary preserves accepted, worker,
implementation, test, review, and terminal milestones as immutable Activity
records with exact principal/objective/task/run causality. It has no arbitrary
summary or transcript field; display summaries are Gateway-authored. Historical
active milestones are excluded from focus and active counts after a structured
terminal fact for that objective.

Only `objective.completed` with persisted
`firstmate.completion-evidence.v1` may wake an additional assistant-only row in
the provider-native Magi store. Activity events never become Chat content; the
model receives only bounded objective/check/artifact facts and the resulting row
uses ordinary native HTTP/replay/WebSocket delivery. See
[`magi-firstmate-execution-v1.md`](magi-firstmate-execution-v1.md) for the event,
evidence, retry, and restart contract.

## Bootstrap, restart, and conflict policy

Structured producers own retry and send authenticated events; Gateway owns the
durable principal-scoped replay. Duplicate event identity plus identical bytes
is idempotent, while identity reuse with changed semantics conflicts. Execution
objective/run causality is immutable, terminal events cannot be extended, and
an older complete decision projection cannot reopen newer state. A disappearing
keyed decision means only `decision.resolved`, never approved/rejected or
objective completion.

Gateway startup performs bounded recovery of its own pending native rows and
completion-report claims. It does not launch an Activity reconciler, enumerate
activity principals, run `fm-fleet-snapshot.sh`, read an outbox, or install a
replacement timer. The retired activity poll interval/disable environment
controls no longer exist. The historical bootstrap/cursor policy remains
implemented only inside the explicitly invoked legacy adapter.

## Delivery and observability

Authenticated, principal-scoped endpoints:

- `GET /api/v1/conversations/{target}/replay?after=…`
- `GET /api/v1/conversations/{target}/turns/{turn_id}`
- `POST /api/v1/conversations/{target}/turns/{turn_id}/assistant-messages`
- `GET /api/v1/activity` (persisted replay; retained `reconcile` input is ignored)
- `GET /api/v1/activity/snapshot` (bounded current projection, active/decision focus, summary, and replay cursor)
- `GET /api/v1/activity/replay` (durable SQLite replay only; no source or terminal I/O)
- `POST /api/v1/activity/catch-up` (persisted replay only)
- `GET /api/v1/diagnostics/soak`

Conversation `replay?after=` pages the tenant-local, append-only `conversation_changes.change_sequence` ledger, not render-order `sequence_index` and not history pagination. Every visible message insertion or revision commits a new change identity; replay returns the current projection for each ledger entry. Thus a progress message committed later at render index 900 is delivered after an already observed primary response at render index 999, and duplicate transports still reconcile by stable message id and revision. The cursor survives restart, begins at `-1`, and a cursor ahead of that principal's ledger fails closed. Authoritative HTTP refreshes replace the bounded conversation window, and every new captain WebSocket connection starts with that current bounded window before tracking `(message revision, turn status, lifecycle revision)` deltas; therefore an in-place revision at an already observed message sequence is replayed after reconnect.

The existing WebSocket remains backward compatible. Activity is sent only when the authenticated control frame opts in with non-negative `activity_after`; the chat delivery loop reads durable rows only and never waits on Firstmate. Activity record `sequence` is stable insertion order, while `delivery_sequence` is an append-only change cursor, so in-place decision/lifecycle revisions are replayable after disconnect. Each change entry carries the record's current projection (an older delivery entry can therefore repeat a causally identical newer revision), and `activity_records` carries the same id/revision/change rows as HTTP replay.

`CanonicalActivity.ts` strictly validates and merges snapshot, HTTP, and socket rows by stable id/revision. A delayed snapshot cannot replace a newer revision or summary; a complete focus projection can remove a stale cached non-terminal fact without inventing its outcome. A historical `before` page is admitted only after replay reaches that page's `snapshot_cursor`, because the page intentionally omits newer non-focus rows and must never checkpoint past them. Its bounded cache repeats the authenticated principal and is cleared synchronously in memory before an account change can paint. Foreground/reconnect and pull-to-refresh recover from the persisted cursor. The native activity sheet pages bounded records, exposes exact keyed decisions through their existing Attention item identity, and labels source/network interruption without interpreting it as completion.

Diagnostics contain bounded counters, classifications, cursors, lag, revisions, and source health. They never retain prompts, assistant prose, raw source payloads, terminal bytes, tool I/O, or exception detail that could contain paths/credentials. Unknown terminal truncation remains `null`, not a fabricated healthy `false`.

## Explicit non-claims

This contract does not replace canonical chat, make Herdr lifecycle changes, create a complete worker transcript, or add a broker. It does not prove notification timeliness, background execution, app-store readiness, or physical-iPhone acceptance; those remain release/soak gates.
