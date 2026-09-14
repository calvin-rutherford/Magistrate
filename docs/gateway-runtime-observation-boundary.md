# Gateway runtime observation boundary

Status: active architecture for native Magi and structured execution.

## Invariant

A Gateway read must not start, stop, restart, signal, adopt, or inspect an
execution process. Runtime lifecycle belongs to Firstmate/Herdr and is entered
only by an authenticated, explicit execution action. Gateway observation is a
projection of durable structured facts:

```text
firstmate.submit_objective (authorized write)
       -> magi_objective_submissions

firstmate.execution-event.v1 (authenticated push)
       -> firstmate_execution_objectives/events -> activity_records

firstmate.decision-events.v1 (authenticated complete push projection)
       -> firstmate_decision_events/decisions -> Attention

firstmate.completion-evidence.v1 (inside a terminal execution event)
       -> persisted evidence -> optional native Magi completion report

GET health/runtime/fleet/agents/activity/attention/notifications/native-chat
       -> SQLite projections and static configuration only
```

No read-side timer fills these tables. Producers retry their authenticated push
contracts, and clients replay the durable Gateway state. In particular, the
Gateway does not run `fm-fleet-snapshot.sh --json`, use Herdr as a process
registry, or infer lifecycle from terminal output.

## Corrected causal chain

The pull lifecycle arrived with the canonical-activity work represented by
`5c34463`/`c2f11fd`. Gateway startup scheduled a roughly 15-second Activity
reconciler, while health, runtime, Fleet, agents, migration, Attention, and
recent-activity reads could independently reach the same
`FirstmateClient.get_snapshot()` call. That call launched
`fm-fleet-snapshot.sh --json` in a new session. Cancellation, timeout, or output
overflow then sent `SIGKILL` to the child's whole process group. A fast,
successful snapshot masked the defect; the visible restart/kill required a
slow or cancelled snapshot and an execution descendant sharing that group.

The counterfactual is now testable: repeated startup/read/notification traffic
with the same persisted rows leaves a separately started process at the same
PID/session/start time, and a manually stopped process stays stopped. Structured
execution ingestion already provided disconfirming evidence for a claimed need
to poll: it persisted progress/completion and woke Magi without Herdr or a
terminal snapshot. The explicit objective tool likewise demonstrates that
intentional delegation remains possible without granting observation an
execution lifecycle.

## Persisted Fleet/runtime projection

`gateway/app/structured_runtime.py` is the read model. It joins only
owner-qualified rows from:

- accepted/submitting `magi_objective_submissions` created by the closed
  `firstmate.submit_objective` tool;
- `firstmate_execution_objectives` and ordered
  `firstmate_execution_events` accepted by the execution-event API; and
- pending/answering `firstmate_decisions` accepted by the decision-event API.

The projection is bounded and fails rather than silently truncating its source
set. Fleet rows carry stable objective/task/run identity and persisted phase.
`GET /api/v1/agents` is a compatibility-shaped list of active structured runs;
it deliberately carries no pane, PID, harness, or model claim. A terminal
execution event removes a run from that active list but remains in Activity.
Migration requests bind a structured task/run and never inspect or stop a pane.

`GET /api/v1/health` and `GET /api/v1/runtime` separately report:

- Gateway readiness;
- static native-model provider configuration, without a provider request;
- authenticated structured event ingress readiness;
- static availability of the explicit `tasks-axi` delegation interface;
- persisted runtime status and last accepted execution-event time; and
- Herdr as `not-probed`, with no fabricated version, protocol, or connection.

`gateway_is_runtime_parent: false` and `live_process_probe: false` are contract
statements, not the result of inspecting a process table. A null event time
means that no owner-qualified event is persisted; it does not mean a worker is
stopped. Likewise, persisted `active` means an accepted nonterminal lifecycle,
not proof that a process is currently alive.

## Write boundaries

The only normal process-starting execution edge is the command-authorized
`firstmate.submit_objective` tool. Its `tasks-axi add` subprocess is bounded,
receives a private body file and minimal environment, and is invoked only after
the model selected the closed tool and host authorization admitted it. A retry
uses the durable objective idempotency record rather than dispatching again.
Cancellation or a bound failure may signal only that exact child; Gateway never
signals a process group.

Progress enters at `POST /api/v1/firstmate/execution-events` with `response` or
`command` scope. Decisions enter at
`POST /api/v1/firstmate/decision-events` with the same scopes. Both contracts
are strict, bounded, principal-derived, and idempotent/conflict detecting. The
decision endpoint accepts a complete source projection so disappearance closes
a hold without a Gateway poll. The answer action remains a separate explicit,
confirmed command and reads exact answer bytes from canonical Native Chat.

## Read routes

The following routes never invoke Firstmate or Herdr:

- `GET /api/v1/health`, `/api/v1/runtime`, `/api/v1/fleet`, and
  `/api/v1/agents`;
- Activity GET/replay/snapshot and `POST /api/v1/activity/catch-up` (the retained
  `reconcile` input is ignored and reported as `persisted-only`);
- Attention and notification reads;
- recent activity (persisted structured objectives plus GitHub's provider
  adapter); and
- native Magi history, replay, diagnostics, and WebSocket delivery.

Gateway startup performs native-store recovery only. It registers no Firstmate
activity reconciler, no execution observation loop, and no replacement timer.
The retired activity poll interval and reconciler-disable controls were
removed and must not be reintroduced.

## Retained compatibility inventory

The legacy terminal conversation stack remains available only when the
mutually-exclusive `MAGISTRATE_LEGACY_CHAT_ENABLED=true` rollback mode is
selected:

- `HerdrClient` CLI/RPC, history parsing, prompt/key/interrupt/rename methods,
  and the terminal-ingest helpers in `main.py` support the retained
  `conversation.v1` captain and worker routes. Every HTTP/WebSocket entry to
  that stack checks the legacy flag, including worker targets. `HerdrClient`
  itself also refuses CLI/RPC access when legacy mode is off.
- `FirstmateClient.get_snapshot`, `get_attention_items`, and its old snapshot
  normalization helpers remain for rollback fixtures. `get_snapshot` refuses
  before filesystem/process access outside legacy mode.
- `FirstmateActivityAdapter.reconcile` and its snapshot/journal readers remain
  as a migration/rollback adapter for historical activity tests and explicitly
  selected legacy operation. No startup task or HTTP route calls it, and the
  adapter returns disabled outside legacy mode.
- The legacy Voice Move service is Herdr-backed and therefore rejects before
  dispatch outside legacy mode.

These remnants are not a second observation authority. Normal Fleet, Activity,
Attention, notifications, migration, health, runtime, delegation, and native
chat do not import their state from a terminal or snapshot.

## Lifecycle validation

`gateway/tests/test_runtime_read_boundaries.py` protects startup, repeated UI
reads, task assignment stability, process identity, the no-process-group rule,
pushed decision projection, and explicit delegation. Native-chat independence
is also exercised in `gateway/tests/test_magi_native_chat.py`; execution ingress
and completion wake behavior are covered by
`gateway/tests/test_firstmate_execution.py`.

Any real Herdr lifecycle experiment must use the guarded, named
`fm-herdr-lab.sh` helper and a non-`default` session. A live diagnostic, if ever
added to the product, must be an explicit Advanced/Diagnostics operator action
with a risk warning; it must never run from startup, a normal read, or a timer.
