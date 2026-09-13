# Magi / Firstmate structured execution bridge v1

## Boundary

This additive bridge implements the Activity and verified-completion seams after
Native Chat Phase 1. It does not select model tools, submit objectives to
Firstmate, answer decisions, read terminal state, or change normal Chat/Voice
submission.

- **Activity** receives durable structured execution milestones.
- **Chat** receives none of those event rows or source prose.
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

The Step 9 submit-objective handler should call the same
`FirstmateExecutionService.ingest` seam after Firstmate has returned its stable
accepted task/objective identity. Model-generated arguments must not be exposed
to the event seam as evidence: only facts accepted by the trusted Firstmate
adapter belong here.

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

## Non-claims

This bridge does not prove that Firstmate performed work merely because a model
asked it to. It accepts completion only at the structured producer boundary and
does not infer phases from prose, pane state, PR text, or a harness stop. It does
not implement Step 9 tool selection/submission, Step 11 Attention decisions,
streaming, deployment, or physical-device acceptance.
