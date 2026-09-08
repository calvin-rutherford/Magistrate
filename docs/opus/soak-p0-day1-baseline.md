# Opus program — SOAK P0 Day-1 baseline and traceability

This is the compact Magistrate-side execution baseline derived from `/home/spectre/firstmate/data/opus-program.md`. It records intent and evidence; it does not change release authority.

## Baseline

| Field | Value |
|---|---|
| Program start (T0) | `2026-09-07T22:59:09-05:00` |
| Deadline | `2026-09-14T22:59:09-05:00` |
| Magistrate code baseline | `9492b7c87a710f4359e5cd5d7bc641eae644f76b` |
| Day-1 objective | Durable tenant-scoped objective/activity lifecycle plus a real Firstmate semantic captain-event producer/consumer path |
| Release posture | **NO-GO** until required gates pass |
| Paid-spend authorization | **USD 0** |
| Development concurrency ceiling | Two workers |
| Physical iPhone acceptance | Not claimed by this implementation |

The paired delivery lanes are:

1. Magistrate core (`fm/magistrate-soak-p0-core-p1`): canonical lifecycle/activity persistence, source consumer, replay/delivery, diagnostics, secure client cache boundary, migrations, tests, and architecture contract.
2. Firstmate producer (`fm/firstmate-soak-p0-outbox-p2`): additive `fm-captain-event.v1` semantic outbox and real trusted producer call site. Magistrate was reconciled against the producer's exact validated PR head `2af0d17014cb2e244aa441bfe6df16c4f630475b` (which retains the instructed `9d9abe7c` contract head and adds its validated leading-hyphen invocation fix), published for review as [Firstmate PR #3982](https://github.com/kunchenguid/firstmate/pull/3982); external checks were still pending when this evidence was recorded, and neither the hash nor open PR is a shared-install or merge claim.

Objective ids, run ids, conversation turns, canonical message ids, Firstmate task ids, worker incarnations, and source-event ids are intentionally distinct. Causal references join them; no layer aliases one identity into another or embeds Magistrate ids in model-visible text.

## Requirement traceability

| Program / corrective requirement | Magistrate implementation | Evidence |
|---|---|---|
| Save an objective before provider work | `conversation_store.record_prompt` persists prompt, turn, objective, run, and reserved primary message atomically | `gateway/tests/test_conversation_store.py`, `gateway/tests/test_structured_responses.py` |
| Actual progress and more than one assistant message | Idempotent assistant-message reservations plus independent gap-free `magi.event.v1` ledgers | `test_one_objective_can_emit_ordered_progress_messages_before_its_stable_final_reply` |
| Real semantic Firstmate source, no terminal inference | Strict fleet/branch/`fm-captain-event.v1` adapter; pane provenance and unknown audience fail closed | `gateway/tests/test_canonical_activity.py` |
| Durable cursors, hashes, restart recovery | `activity_sources`, `canonical_source_events`, transactional projection/cursor advance, immutable-prefix hashes, startup cadence | restart, ordering, conflict, gap, torn-tail, and bootstrap tests in `test_canonical_activity.py` |
| Truthful lifecycle and decisions | Monotonic lifecycle state/revision; exact decision keys; disappearance means only resolved | structured awaiting-user and activity decision-resolution tests |
| Replay and WebSocket catch-up | Independent append-only conversation/activity change cursors, opt-in `activity_after` delivery, and non-visual frontend catch-up adapter | Gateway `test_conversation_store.py`, `test_canonical_activity.py`, `test_structured_responses.py`, and frontend `canonical-activity.test.ts` |
| Existing owner data survives migration | Additive columns/tables, deterministic legacy objective/run ids, primary message identity preservation | `test_schema_migration_and_event_ledger_are_additive` |
| Tenant isolation from authenticated authority | Every new public activity/lifecycle path derives ownership from `Principal.user_id`; contracts reject extra client `user_id` | two-tenant and HTTP ownership tests in `test_canonical_activity.py` |
| Cache isolation across auth lifecycle | Principal-qualified canonical/pending keys; synchronous memory clear and awaited old-key eviction | `frontend/tests/auth-lifecycle.web.test.js` |
| P0 loss mode is observable without leaking terminal data | Bounded ingest/source counters and classifications, unknown truncation represented as null | promptless-attribution and soak-diagnostics tests |
| Preserve legacy/friend-beta foundations | `conversation.v1`, `magi.event.v1`, stable ids, terminal fallback before semantic ownership, keyed Attention, and append-only branch outcomes remain in place | full Gateway/frontend regression suites |

The detailed runtime, bootstrap, security, and delivery contract is [`../canonical-lifecycle-activity-v1.md`](../canonical-lifecycle-activity-v1.md).

## Day-1 producer/consumer contract evidence

On 2026-09-08, a temporary default-off Firstmate home was explicitly enabled and the delegated real `bin/fm-captain-event.sh` published one `primary.message` and one `worker.final`. This Gateway consumed the exact canonical `state/captain-events/events.jsonl` records through the versioned reader, transactionally persisted two completed canonical activity records (`primary.message`, `worker.final`) without treating Pi's per-turn `final` stop reason as task completion, advanced its durable cursor to 2, and only then produced the exact matching `acks/magistrate.json` receipt through sequence 2. The repeated hermetic check against exact producer head `2af0d17014cb2e244aa441bfe6df16c4f630475b` returned `status=available`, cursor/ack `2`, and a receipt event id equal to the second canonical row. The hermetic reader/commit/ack behavior is retained in `test_versioned_reader_is_activated_explicitly_and_acked_only_after_ingestion`.

This is local software contract evidence, not evidence that the shared Firstmate installation was upgraded, the live Gateway was deployed, a long soak passed, or a physical iPhone received the events.

## Gate posture

Implementation tests are software evidence only. Release still requires the program's CI, deploy, notification/background, replay/restart, and physical-device gates. This lane must not represent a simulator/browser result as physical-iPhone acceptance, initiate paid traffic, push the default branch, or merge a pull request.
