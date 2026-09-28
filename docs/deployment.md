# Native Gateway deployment

The executable release authority is [Production security and operations](production-security-operations.md)
and `scripts/deploy_magistrate.sh`. Deploy only Gateway plus the exported Expo
frontend. Pi/Django/terminal transports are not rollback targets; historical
ownership protocols are retained for data/migration reference only.

## Private production configuration

Use the reviewed persistent deployment checkout, conventionally
`/home/spectre/firstmate/projects/Magistrate-deploy`. Its service-owned,
non-symlink `gateway/.env` must be mode `0600`. Never copy it from a dirty
development checkout or source it as shell code. The preflight parses literal
assignments, rejects duplicates and does not inherit interactive credentials.

Set `MAGISTRATE_ENV=production`, independent random bootstrap and Fernet
secrets, exact HTTPS CORS origins and at least one configured candidate in the
validated routing catalog. See `.env.example` and
[model routing](model-routing-cost-failover.md). No provider secret belongs in
`EXPO_PUBLIC_*`. Remove obsolete transport selectors rather than configuring
an alternate conversation architecture.

Choose exactly one persistence backend:

- **SQLite, single instance:** absolute external `MAGISTRATE_DB_PATH`, an
  existing service-owned mode-0600 file with private parents.
- **PostgreSQL, multi-instance:** `MAGISTRATE_DATABASE_URL` with TLS and
  least-privilege credentials, plus a pre-provisioned, service-owned mode-0700
  `MAGISTRATE_STATE_DIR` on shared POSIX storage. For each deployment,
  acknowledge a restorable managed snapshot with
  `MAGISTRATE_POSTGRES_BACKUP_CONFIRMED=true`. See
  [identity/tenancy/data lifecycle](identity-tenancy-data-lifecycle.md).

Private object and avatar roots must be persistent, normalized, symlink-free,
external to the release checkout and non-overlapping. Only the avatar root is
publicly mounted; never expose private objects through a static server.

Additional activation authorities remain separate:

- [Apple/Google sign-in](provider-sign-in.md): verified native/web audiences,
  exact redirects, SecureStore/native refresh and same-site HttpOnly web cookies.
- [GitHub App](github-app-activation.md): owner-bound installations and
  server-only App/token authority; no operator-wide forge credential fallback.
- [Billing](billing-and-credits.md): external protected price catalog, live API
  and signed webhook secrets; no entitlement from redirects.
- [Hosted execution](hosted-execution.md): external mTLS isolation backend,
  pinned image, enforced worker network/resource policy and scoped token broker.
  Restricted local execution must never be represented as public tenant isolation.

## Guarded release and recovery

Run `scripts/deploy_magistrate.sh` from a trusted shell. It locks, refuses dirty
or divergent work, fast-forwards without discarding ahead commits, installs
locked dependencies, runs fail-closed preflight and validates any required pinned
producer. Configuration imports do **not** migrate/connect to the database.
Before startup migrations it takes a verified SQLite backup with checksum,
table-count manifest and commit sidecar, or requires the PostgreSQL snapshot
acknowledgement. It exports/checks web assets and restarts only the dedicated
Gateway unit, never worker/Herdr lifecycle.

Only `/readyz` **HTTP 200** is deployment success. `/livez` proves process
liveness; 401/403 or a configured provider do not prove readiness/upstream health.
Timeouts leave recovery artifacts for the operator, not an automatic rollback.
The checkout/dependency update is not atomic blue/green deployment.

On the trusted host, `MAGISTRATE_TRUSTED_SMOKE=1` additionally exercises session
issuance and authenticated product reads without printing authority. The
restricted-beta public HTTPS/WSS smoke uses a separate grant and revokes it;
see [Friend Beta readiness](friend-beta-release-readiness.md). Neither is a
physical-device/TestFlight result.

Use the tested backup/restore-to-new-destination commands and rollback/session
revocation procedure in [the operations runbook](production-security-operations.md).
Recovery includes database, private objects/avatars, exact release artifacts and
separately escrowed key versions. Never overwrite a live database or select a
legacy transport to work around a failed release.

## Manual-only deployment workflow

`.github/workflows/deploy-demo.yml` has no push trigger. Only an operator's
explicit confirmed dispatch may use its guarded SSH path. Secrets are
`MAGISTRATE_DEPLOY_HOST`, `MAGISTRATE_DEPLOY_USER`,
`MAGISTRATE_DEPLOY_SSH_KEY` and pinned `MAGISTRATE_DEPLOY_KNOWN_HOSTS`.
The workflow never receives bootstrap/session authority. Missing secrets or
unsafe checkout state fail closed; do not reset, stash or force-push away work.
The approved private SSH path remains the manual recovery channel.

The reverse proxy must preserve WSS Upgrade/Connection headers. Review the
systemd/nginx/logging/alert templates in `runtime/`; their presence is not
installed monitoring, alert-delivery or live-deployment evidence.
