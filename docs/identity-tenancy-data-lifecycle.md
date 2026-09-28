# Identity, tenancy, projects, and data lifecycle

## Authority and isolation

The Gateway principal (`Principal.user_id`) is the tenant boundary. Public project, repository, account-deletion, chat, upload, objective, Fleet, Activity, Attention, notification, execution-setting, and provider routes derive it from the bearer; none accepts a client owner id. Project/repository lookups return the same 404 for a missing id and a foreign id.

Each account gets one deterministic personal workspace. This is an ownership container, not a claimed organization/team product. Projects are durable, opaque records and may be standalone or have one or more GitHub repository bindings. A binding is metadata and does **not** claim that GitHub authorized it. The existing gh-axi, quota-axi, Jira, and Teams adapters use deployment-level operator authority, so their endpoints/feed contributions are explicitly bootstrap-owner-only; non-owner tenants receive no operator provider data. A future provider-native per-principal client may safely replace that restriction. Accepted Magi objectives resolve an active project by opaque id, slug, or exact display name and persist `project_id`; an unmatched historical/free-form project label remains valid and unbound.

The tenant schema includes owner-qualified billing/credit and encrypted project-memory ledgers so those future integrations cannot begin from global rows. No API currently invents a balance, subscription, or memory value.

## API

- `GET/POST /api/v1/projects`
- `GET/PATCH/DELETE /api/v1/projects/{project_id}`
- `POST /api/v1/projects/{project_id}/repositories`
- `DELETE /api/v1/projects/{project_id}/repositories/{repository_id}`
- `DELETE /api/v1/account` with JSON `{"confirmation":"DELETE <authenticated-user-id>"}`

A project with objective history must be archived, not deleted. Account deletion is different: it permanently erases current and retired conversation rows, messages, projects/repositories, objectives, Fleet/Activity/Attention projections, uploads/artifacts, notification state, OAuth/session authority, billing/credits, encrypted execution secrets, and project memory for the authenticated principal. It never accepts a target account id. Upload/avatar bytes are atomically renamed to a private same-volume quarantine before the database transaction; a rollback restores them, while a commit unlinks them. The response's random keyed deletion id is not persisted.

## Database backends and multi-instance operation

SQLite remains the zero-rewrite development and single-instance deployment path:

```sh
MAGISTRATE_DB_PATH=/var/lib/magistrate/magistrate.sqlite3
```

It is explicitly reported as `multi_instance_safe: false` and must not be placed on a shared network filesystem.

PostgreSQL is the supported multi-instance path. Every Gateway store uses `gateway/app/persistence.py`; parameterized legacy SQL is translated at that narrow seam. PostgreSQL preserves SQLite's 64-bit integer semantics, database uniqueness/check/foreign-key constraints, and serializes existing `BEGIN IMMEDIATE` write sections with a transaction-scoped advisory lock. Startup schema migration is guarded by a separate transaction-scoped advisory lock, so concurrent instances converge on one version.

```sh
MAGISTRATE_DATABASE_URL='postgresql://magistrate:...@db.internal:5432/magistrate'
MAGISTRATE_STATE_DIR=/var/lib/magistrate   # private shared POSIX upload/quarantine volume
MAGISTRATE_SECRET_KEY='...generated Fernet key...'
```

Use TLS and a least-privilege database role in production (for example, `sslmode=verify-full`). `MAGISTRATE_STATE_DIR` is still required because upload bytes are filesystem objects. Every Gateway instance must mount the same private POSIX volume at the same absolute path; account deletion relies on same-volume atomic rename, so instance-local disks are not a supported multi-instance upload configuration until an object-store adapter exists. The repository's PostgreSQL CI smoke starts two Gateway processes concurrently against one database/state directory, then proves separate project/repository/chat/upload/secret rows and current schema health.

`GET /api/v1/health` includes content-free database evidence: backend, current/expected migration version, query latency, and whether the backend is multi-instance safe. SQLite reports `PRAGMA quick_check`; PostgreSQL reports only connection/query reachability (not a fabricated storage-integrity check). It contains no path or DSN.

## Migrations, transactions, and rollback

`schema_migrations` is the ordered authority. `db.apply_schema_migrations()` wraps each additive migration in a savepoint and records its version only after success. A failed migration rolls back both DDL/data and its version row. Startup fails closed on a migration error.

Before deployment:

1. Stop writes or take a transactionally consistent managed-PostgreSQL snapshot / SQLite backup.
2. Deploy one canary and require `/api/v1/health.database.status == healthy` and the expected schema version.
3. Start remaining PostgreSQL instances; the migration advisory lock makes this safe.

Migrations are forward-only. Application rollback means restoring the pre-deploy artifact **without** deleting additive tables. Data/schema rollback means stopping all instances and restoring the pre-migration snapshot; never hand-edit `schema_migrations`. Tests cover migration failure rollback and account-deletion filesystem/database rollback.

## Retention

User content, projects, objective/activity evidence, billing/credit records, and encrypted memory have no silent time-based deletion. They remain until explicit account deletion or a future documented product policy. `account_lifecycle.enforce_retention()` removes only authentication control data: sessions/session families/refresh tokens more than 30 days past expiry or revocation, and provider challenges more than 24 hours past expiry. It never executes from a read path.

## Evidence and activation boundary

`gateway/tests/test_identity_tenancy_projects.py` proves opaque two-tenant project/repository access, transactional migration rollback, bounded auth-control retention, deletion rollback, and selective erasure while preserving the second tenant across Native Magi messages, projects, repositories, objectives, Activity/Fleet source rows, artifacts/uploads, billing/credits, encrypted execution secrets, memory, OAuth state, and sessions. Existing focused suites prove owner qualification for Fleet, Attention, decisions, notifications, execution, canonical Activity, provider auth, and Native Magi replay.

Repository-controlled implementation and CI are **COMPLETE**. Production database provisioning, credentials/TLS, provider-console OAuth approval, App Store signing/review, and physical-device evidence are **BLOCKED_EXTERNAL** activation steps; they do not justify a repository placeholder or a false connected state.
