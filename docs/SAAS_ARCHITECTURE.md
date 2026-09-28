# SaaS architecture and migration composition

Authority: [production matrix](PRODUCTION_STATUS.md). Repository domain contracts
are **COMPLETE** within the boundaries below; infrastructure activation is
**BLOCKED_EXTERNAL**. This is a principal-owned personal-workspace product, not
an implemented enterprise organization/membership system.

## Executable topology

```text
Expo Chat / Voice / Projects
       │ server-verified principal, scoped bearer
       ▼
Gateway ── canonical Native Magi messages ── bounded context + routed providers
       │ closed tools; host-derived identity and confirmation
       ▼
Objective + frozen context + customer-credit reservation
       ├─ restricted local intake: deterministic tasks-axi add / Firstmate wake
       └─ hosted intake: durable controller / mTLS isolated worker backend
       │ authenticated structured events / decisions / measured usage / evidence
       ▼
Persisted Fleet / Activity / Attention / canonical outcome messages
```

Human conversation is only `/api/v1/magi/*` plus `magi_messages` events. No worker
transcript, terminal, Pi transport switch or selectable worker target is Chat.
Reads project durable facts; they do not probe/supervise workers. Hosted execution
has an explicit **write-side** controller, leases and reconciliation; this does
not authorize execution timers in health/Fleet/Activity/Attention reads. Local
mode's bounded startup recovery is likewise a write-side admission operation.

## Merged domain authority

| Domain | Executable authority | Boundary |
|---|---|---|
| Identity | `provider_auth.py`, `auth.py`, `onboarding.py` | Verified provider subject, no email auto-linking; welcome/name/GitHub OAuth/signed subscription onboarding |
| Projects | `projects.py` | One personal workspace per principal, opaque owner-qualified projects and repository bindings; metadata binding alone grants no GitHub authority |
| GitHub | `github_app.py` | Principal-bound installations and current authorized repositories; old global forge subprocess service deleted |
| Credits | `billing.py`, `billing_api.py` | Signed webhooks, integer ledger, pre-intake reservation and measured settlement |
| Context | `project_memory.py` | Owner/tenant/org/workspace/project/repository qualification, revisions, tombstones and frozen objective context |
| Routing | `magi_routing.py`, `magi_providers.py`, `execution_routing.py` | Native vendor adapters and operator USD budgets separate from customer execution credits and harness lifecycle |
| Execution | `magi_firstmate_tools.py`, `firstmate_intake.py`, `hosted_execution.py` | Exclusive local/hosted intake; hosted objective-bound bearers and provider-neutral isolation API |
| Files | `uploads.py`, `perception.py` | Private POSIX objects, quotas, digests, consented non-executing perception; no object-store cloud SDK |
| Recovery | `account_lifecycle.py`, `scripts/storage_ops.py` | Owner erasure and file quarantine; new-target SQLite backup/restore; external PostgreSQL snapshot/PITR |
| Observation / notification | `structured_runtime.py`, `telemetry.py`, `push_receipts.py` | Durable product projections, content-free logs/operator metrics and separately reconciled provider receipts |

Component details: [identity](identity-tenancy-data-lifecycle.md),
[hosted execution](hosted-execution.md), [security operations](production-security-operations.md).
When older component prose describes a subsequently merged domain as future or
absent, the current code and this production matrix take precedence.

## Persistence and ordered composition

All Gateway stores use `persistence.py`. SQLite is **single-instance only**, not
a network-filesystem multi-writer topology. PostgreSQL is the multi-instance
path selected by `MAGISTRATE_DATABASE_URL`. It translates the legacy parameterized
SQL seam, uses transaction-scoped advisory locks for serialized writes and a
separate migration lock. Concurrent startup and selective two-tenant erasure are
exercised by `postgres-persistence` against a disposable PostgreSQL 16 container.
This is not a managed-cloud restore drill.

`db.py:_SCHEMA_MIGRATIONS` and `schema_migrations` own forward-only versions:

1. legacy baseline;
2. projects and tenant lifecycle;
3. provider onboarding and billing;
4. GitHub App installations/repositories;
5. customer credit billing ledgers;
6. scoped project context;
7. hosted execution;
8. files and perception;
9. durable push ticket/receipt delivery accounting.

Each migration records its version only after its savepoint succeeds. Retained
conversation/Pi rows are not dropped by upgrade. An explicit account erasure is
a separate authenticated destructive action that also removes that owner's
historical rows. Preserve message IDs/revisions, objective/task/run causality,
immutable event identities, context digests, reservation keys and evidence.

`test_release_restore.py` upgrades a populated version-3 fixture, seeds all
merged domains, runs the actual SQLite backup/restore entrypoints, compares
**every table/row**, repeats initialization, verifies integrity/FKs and encryption,
restores private object bytes separately, and exercises pending-chat recovery.
The older v1 database fixture and migration rollback tests remain mandatory.
The PostgreSQL contract covers concurrent domain persistence/erasure; an actual
PostgreSQL backup/restore with secret escrow and a shared-volume snapshot is still
an external activation checkpoint.

## Deployment and rollback

Deploy only FastAPI Gateway plus exported Expo assets. PostgreSQL instances must
share the same private POSIX state volume at the same absolute path: upload and
erasure logic uses same-volume atomic rename. No S3/bucket variable substitutes
for that implementation. Provision DB/state outside the release checkout.

Back up **before migration**, include files and required secret versions, canary
against `/readyz` HTTP 200 and authenticated schema health, then promote. Restore
only into a new isolated DB/state target; an older snapshot may resurrect revoked
sessions, so close traffic and review/reissue authority before reopening. There
is no automatic data-loss rollback or schema downgrade. The guarded update
script is not an atomic blue/green deployment manager.

Django/Celery and root Compose are lab-quarantined, old launch/rsync scripts refuse
production, and unauthenticated AR is removed. These are not rollback targets.
The retained Pi adapter is regression-tested, never human-conversation authority.
See [activation](PRODUCTION_ACTIVATION.md), [protocol](CLIENT_PROTOCOL.md) and
[billing](BILLING_MODEL.md) for exact release boundaries.
