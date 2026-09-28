# SaaS architecture and migration composition

Authority: [production matrix](PRODUCTION_STATUS.md). **SaaS convergence: FAILED.**
This distinguishes the executable baseline from required production contracts.

## Executable baseline

```text
Expo Chat / Voice ── authenticated Native Magi API ── SQLite canonical messages
                                   │
                            provider-native model
                                   │ closed, scope-bound tools only
                            durable objective intake
                                   │ deterministic task + one bounded wake
                              Firstmate scheduler
                                   │ authenticated structured pushes
                         events / decisions / evidence
                                   │ persisted projections
                         Fleet / Activity / Attention
```

Gateway is the identity, API and persistence boundary, not a scheduler. Normal
startup/health/product reads project durable facts and never scrape terminals,
snapshot Herdr, signal a process or install a reconciliation timer. One-shot
write-side recovery of interrupted queue/wake commits is distinct from reads.
Notification reconciliation may deliver persisted Attention changes, not execute
work. A model's completion prose is not evidence that a worker completed work.

The current durable human identity is a Gateway principal; native messages,
objectives, events and decisions carry owner qualification. Current Firstmate,
GitHub CLI access and some controls remain deployment-owner resources. This is
not tenant runtime isolation. Friend Beta shared-runtime acknowledgement does
not make it so. Legacy Django/Channels/Celery models and Postgres migrations are
independent; they are not the Native Magi data authority. Retained Pi tables are
historical state, never an alternative human conversation route.

## Required production domain boundaries

| Domain | Required authority | Integration owner |
|---|---|---|
| Principal / membership | Verified provider subject → internal principal; organization/project membership resolved server-side on every access | A1 / A2 |
| Project / repository | Tenant-authorized project plus installation/repository binding; no global service-identity fallback | A1 / A3 |
| Billing / entitlement | Durable customer mapping, verified payment events, ledger and reserved budget; no client-computed balance | A4 |
| Context | Tenant/project ACL, provenance, immutable versions, deletion/retention and bounded model selection | A6 |
| Routing | Host policy chooses permitted provider/model/harness with budget and capability evidence; request/model JSON cannot select authority | A7 |
| Execution | Durable owner/project objective and causality, isolated runtime mapping, Firstmate queue/scheduler | A5 / A11 |
| Files | Private owner/tenant metadata and durable blob identity; independently verified storage/processing states | A10 |
| Product | Chat / Projects / Fleet / Activity / Attention; client never reconstructs lifecycle from prose | A8 / A9 |
| Release | Composition tests, schema export, migration/restore rehearsal and candidate evidence | A12 |

These are integration constraints, not invented endpoints or already-existing
organization tables. Each workstream must supply actual routes/schemas before
its row changes from FAILED. No caller-supplied owner/tenant field becomes an
authorization source. No new tenant feature may default an unknown membership to
`default_user`, an operator repository, or a shared worker HOME.

## Data compatibility contract

1. Preserve existing canonical message IDs, client idempotency keys, revisions,
   timestamps, reply edges, objective/task/run causality, source-event identities,
   confirmation revisions and evidence hashes. Replays cannot re-charge, re-wake
   or silently change an accepted event's facts.
2. Add fields/tables/indexes without dropping or rewriting historical
   `conversation_*` / `pi_*` data. A destructive backfill or principal merge
   requires a separately approved migration and restore plan.
3. Introduce schema versions at external boundaries. Unknown required variants
   fail closed. Extra model-selected authority is rejected, not ignored.
4. Upgrade Gateway before clients that require new fields. Readers tolerate
   explicitly optional additive fields; they do not fabricate unobserved values.
   If semantics break compatibility, version the protocol and test both readers.
5. Model-provider errors remain durable truthful failures; billing settlement,
   queue acceptance and task completion each have distinct transaction ledgers.

## Migration ownership and order

`gateway/app/db.py:init_db()` is the current additive schema authority. Uploads,
notifications and OAuth transactions also initialize additive tables in their
own modules. There is no numbered, globally composed migration runner yet.
Every schema owner must list its tables, indexes, foreign keys, backfills,
transaction boundaries and old-schema fixture in the integration handoff.

A12 composition order:

1. Take and verify a pre-upgrade online backup plus storage/secret-version
   manifest; record the exact deployed Git revision.
2. Compose A1 identity/membership parents before GitHub installations, ledger,
   context and storage children; execution/routing references follow their
   identity/entitlement contracts. Unique/index changes require collision tests.
3. Run upgrade against a populated pre-feature DB, then run initialization twice.
   Test concurrent startup if the merged migrator permits it; otherwise enforce
   a single migration owner before application startup. Never assume concurrent
   `ALTER TABLE` is safe merely because `CREATE TABLE IF NOT EXISTS` is used.
4. Assert exact retained rows/bytes, ownership, evidence hashes, foreign keys,
   native replay and encrypted credential readability. Add seeded billing,
   tenant/context/file rows as those implementations merge.
5. Simulate interrupted chat, queue publication, event generation and decisions.
   Recovery cannot create duplicate work or falsely finish a pending response.
6. Restore to a new isolated target and smoke with the intended code revision.
   Rollback means the matching code + whole consistent state snapshot + required
   keys/storage, not deleting inconvenient events or remapping an owned turn.

`gateway/tests/test_release_restore.py` seeds native/legacy messages, pending
chat, objective submissions, accepted/completed events, verified evidence,
Activity, pending decisions, OAuth ciphertext and file metadata. It discovers
all current tables and compares all rows through backup → restore → repeated
init, integrity/FK checks and orphan recovery. It is a foundation, not evidence
for unmerged schema additions or cloud recovery. DB backup alone does not contain
upload bytes, a Fernet key, or external provider state.

## Deployment shape and non-goals

The current supported product deploy is FastAPI plus exported Expo web and
persistent SQLite/file state, with Firstmate behind its explicit producer/control
seam. Do not replace it with root Docker compose: that launches the retained
Django broker/workers and mounts the host Docker socket. No high-availability
multi-writer Gateway/SQLite topology is proved here. Scaling/database migration
requires an explicit owner design and locking/recovery evidence, not a new
`DATABASE_URL` value in the existing process.

Activation, backups, DNS, keys and account setup are in
[PRODUCTION_ACTIVATION.md](PRODUCTION_ACTIVATION.md). Protocol ownership is in
[CLIENT_PROTOCOL.md](CLIENT_PROTOCOL.md); security and money boundaries are in
[SECURITY_MODEL.md](SECURITY_MODEL.md) and [BILLING_MODEL.md](BILLING_MODEL.md).
