# Production security and operations boundary

This is the current operational contract; older Pi/terminal rollback instructions
in historical rollout documents are **not executable production guidance**.
This hardening is not a certification of tenant isolation, App Store approval, or
safe execution of arbitrary repositories on the existing shared worker host.

## Threat model and executable controls

| Threat | Implemented boundary / evidence | Remaining authority or limit |
|---|---|---|
| Cross-principal upload theft, forged attachment IDs, replay | Real two-principal bearer tests in `gateway/tests/test_production_security.py`; owner-qualified reads/associations, signed access and revocation | Workspace/project/context and GitHub installation ownership are enforced by their domain stores. Hosted worker isolation additionally requires the external activation evidence in `docs/hosted-execution.md`. |
| Multipart/JSON exhaustion, false Content-Length, slow bodies | `request_boundary.py` measures received bytes before parsers, bounds body time and in-flight requests; private object quotas reserve under SQLite `BEGIN IMMEDIATE` or PostgreSQL transaction advisory lock | HTTP limits are per-process, upload ledger quotas span instances. Apply edge rate/connection/body controls and filesystem quotas for crash orphans. |
| Path traversal, secret-file download, public stored HTML/SVG | Random upload/avatar identities; private upload creation mode 0600; stored-path containment/symlink checks; bounded image-only avatar endpoint; only avatar directory is publicly mounted; nosniff/no-store and sandbox CSP | Signature checking is not decoding, antivirus or content disarm. Backups/volumes need encryption and access control. Same-UID malicious code is outside filesystem isolation. |
| SSRF / provider-key exfiltration | Credential-free HTTPS/443 URL validation, exact operator-controlled provider host allowlist in production, redirects disabled and environment proxy inheritance disabled for model/STT; bounded model response before JSON parse | Do not allow tenant/model-controlled provider endpoints. DNS rebinding requires network-level deny rules for private/link-local/metadata ranges; URL parsing is not an egress firewall. |
| Forge credential exposure | The old operator-wide forge subprocess service is deleted; `github_app.py` uses owner-bound installations and server-only scoped tokens with signed idempotent webhooks | Follow `docs/github-app-activation.md` and the hosted token-broker activation gates. Never mount Gateway HOME or App private keys into repo workers. |
| Prompt injection / tool abuse / malicious repos | Native Chat remains the sole conversation path; closed tools, bound confirmation and pinned Firstmate contracts remain unchanged; no terminal-derived authorization. Existing decision/tool/producer attack suites remain mandatory | Gateway cannot sandbox a worker by documenting it. Separate UID/container/VM, no Docker socket, read-only code, per-workspace mounts, deny-by-default egress and short-lived scoped credentials must be supplied by execution infrastructure. |
| OAuth confusion / session replay | Existing server-verified audiences, exact redirects, one-time challenge/refresh rotation and owner-bound session tests retained; exact production CORS parsing fixes localhost-prefix confusion | Opaque access bearers remain replayable until expiry/revocation if stolen. Native secure storage, HTTPS and device logout are mandatory; this is not hardware binding. |
| Payment spoofing / spend exhaustion | Preserve raw-byte signed, timestamp-checked, idempotent Stripe webhooks and authoritative credit reservations in `billing.py`; routed model budgets remain in `magi_routing.py` | Paid activation needs the external catalog/Stripe evidence in `docs/billing-and-credits.md`. Redirects never confer paid state. This lane does not invent prices, spend or external activation. |
| Supply chain / accidental legacy deployment | Frozen Gateway dependencies, npm lockfile, updated transitive packages/Puppeteer, fail-closed production preflight; retired root launch/rsync/provisioning scripts exit; Compose requires `legacy-lab`; Django refuses production; unauthenticated AR route/client removed | Dependency advisories below remain a release risk. Lab code still has unsafe assumptions and must not receive production secrets or network exposure. |

Upload defaults: 25 MiB/file, 10/message, 50 MiB/message; retained private uploads
250 MiB/500 files per principal, 5 GiB/10,000 globally. `MAGISTRATE_UPLOAD_{USER,GLOBAL}_{BYTES,COUNT}`
can lower/change those budgets. Partial multipart failures discard that batch's
new files. Private paths must not overlap the public avatar root. The operator
must provision disk quotas as defense against crash orphans and repeated avatars.
Do not serve any parent of the private upload directory from nginx/CDN.

## Production configuration and deployment

1. Use only `gateway/app/main.py` plus the exported Expo frontend. Read
   `.env.example`, `gateway/app/production_security.py` and
   `gateway/scripts/production_preflight.py`. `MAGISTRATE_ENV=production` is
   mandatory for deployment; missing/unknown modes cannot accidentally become dev.
2. Service environment and SQLite are service-owned regular files, mode 0600;
   database and uploads live outside the checkout. Choose exactly one backend:
   SQLite for one instance, or `MAGISTRATE_DATABASE_URL` for PostgreSQL with a
   pre-provisioned service-owned mode-0700 `MAGISTRATE_STATE_DIR` on shared POSIX
   storage. Use TLS/least-privilege database credentials. Keep parent directories
   private. Generate independent random bootstrap, encryption and scrape secrets.
   Production bootstrap authority must have at least 32 characters. Never put
   secrets in `EXPO_PUBLIC_*`, command arguments, URLs, screenshots or PR logs.
3. Native Chat is unconditional. Remove obsolete transport flags. Enabling legacy
   Chat/Pi or development auto-session is rejected, not a supported rollback.
4. Review `runtime/magistrate-gateway.service.example` and
   `runtime/nginx-production.conf.example`; these are templates, not evidence of
   an installed service. Configure only the required explicit Firstmate writable
   roots. Keep Gateway on loopback behind HTTPS. Worker networking isolation is
   separate from Gateway's provider allowlist. Run as a dedicated non-root user.
5. Run `scripts/deploy_magistrate.sh` from a reviewed deployment checkout. It
   locks, refuses dirty/diverged work, preserves ahead commits, fast-forwards,
   installs locked dependencies, validates private configuration (including
   routed non-OpenAI providers, activated billing and hosted isolation), verifies
   a required pinned producer, takes an integrity-checked online SQLite backup
   or requires `MAGISTRATE_POSTGRES_BACKUP_CONFIRMED=true` for a restorable managed
   snapshot, exports web assets and restarts **only the dedicated Gateway unit**.
   Configuration imports do not connect or migrate before that recovery point. No worker/Herdr
   lifecycle is involved. First installation must provision the external database
   through an operator-approved initialization before this update-only script.
6. Readiness accepts only HTTP 200 from `/readyz`; 401/403 are **not** successful
   deployment evidence. The optional trusted smoke still exercises authenticated
   product reads. Run `scripts/smoke_magistrate.sh` and the restricted-beta smoke
   only on the trusted host with its private environment.

A failed preflight/build leaves the running process alone but may already have
fast-forwarded the checkout or changed its environment/dependencies. This is not
an atomic blue/green release manager. Use immutable per-release directories for
stronger rollback isolation; never claim an automatic rollback occurred.

## Health, logs, correlation and alerts

- `/livez`: process liveness. `/readyz`: completed startup, readable persisted
  current persisted SQLite/PostgreSQL schema and configured routed provider. Neither runs a model, scheduler, Fleet
  snapshot or runtime probe. `/api/v1/health` remains the authenticated richer
  structured-state view. Configured is not live upstream health.
- Uvicorn must use `--no-access-log --log-config logging.json` from `gateway/`.
  Default HTTP access logs can disclose OAuth query codes. The nginx template
  similarly logs only status, elapsed time and Gateway-generated request ID.
- `X-Request-ID` is generated, never accepted from an untrusted header. The same
  ContextVar flows through async provider/ingress work. JSON operations logs
  contain no body, URL, raw path, headers, owner identity, exception string or
  tool payload. Objective correlation is a stable hash, not model-controlled text.
- `/internal/metrics` requires **independent** `MAGISTRATE_METRICS_TOKEN` bearer
  authority; product read sessions cannot scrape global aggregates. Leave unset
  to disable. The edge template blocks `/internal/` publicly. Scrape privately
  over trusted local networking/TLS and configure the bearer as a secret file.
- Metrics are per-process operation counts/duration sums and persisted intake,
  unobserved accepted-objective, pending-chat and structured terminal-event
  count/elapsed-time aggregates. They are not a live worker queue or p95 latency.
  Reads never initialize/migrate the database or start reconciliation. Routing
  and each provider attempt emit content-free operation spans; authoritative
  spend/reservations remain in the routing/billing ledgers, not guessed metrics.
  `record('billing_webhook', ...)` remains a closed content-free integration seam.
- `set_error_reporter()` accepts a trusted in-process sink receiving the same
  sanitized record, not an exception/body. Sink failure cannot fail a request.
  No arbitrary remote error-reporting URL is accepted.
- Load/review `runtime/prometheus-alerts.yml`, wire a real alert destination,
  test delivery, and add disk space/inode, backup age and external readiness
  alerts in the infrastructure monitor. Templates are not delivery evidence.

## Backup, restore, rollback and retention

`gateway/scripts/storage_ops.py` uses the SQLite online backup API (including WAL
state), integrity checks, SHA-256 manifest and table counts of the snapshot.
Destination directories must exist, be service-owned mode 0700; new backup and
manifest files are mode 0600 and never overwrite existing paths. Example syntax:

```sh
python3 gateway/scripts/storage_ops.py backup /private/state/app.sqlite3 /private/backups/release.sqlite3
python3 gateway/scripts/storage_ops.py restore /private/backups/release.sqlite3 /private/restore/drill.sqlite3
```

These commands are SQLite-only; they never treat PostgreSQL's state marker as a
SQLite database. PostgreSQL needs managed encrypted backups/PITR and restore to a
separate database plus matching shared object-volume snapshot; an acknowledgement
is not restore evidence. See `docs/identity-tenancy-data-lifecycle.md`.

Run regular restore drills against **new private destinations**; compare manifest
counts and authenticated read smoke results using an isolated Gateway with all
notifications/execution side effects disabled. Do not point a drill at real
provider/tool credentials or producer outboxes. Tests exercise backup/restore,
corruption, symlinks, existing destinations and retention traversal.

Keep backup manifests, exact deployed commit, frontend artifacts and encrypted
key-version escrow together in the release inventory. Database backup alone is
not a complete recovery: snapshot private attachments and avatars and retain the
matching encryption key version in a separate approved vault. Never commit any
of these. Encrypt backup storage and apply operator-approved expiration.

Rollback is an operator action: preserve the failed release and its backup,
choose the exact last reviewed **native** release and verify schema compatibility.
Do not switch to Pi/Django or run destructive Git resets in a dirty checkout. If
schema downgrade is unsafe, forward-fix or restore a verified snapshot into a
new external database while the Gateway is quiesced. Never overwrite a live DB
or copy a stale WAL beside a restored file. Revocation/refresh state from an old
backup can resurrect authority; keep traffic closed and revoke/reissue affected
sessions/grants before reopening. No automatic data-loss rollback is implemented.

Unattached-upload retention is deliberately bounded and dry-run by default:

```sh
python3 gateway/scripts/storage_ops.py prune-unattached /private/state/app.sqlite3 /private/state/private_objects --before <unix-seconds> --limit 100
# Review selected count; only then repeat with --apply.
```

Minimum age is one day. Attached files and canonical messages are not deleted;
modern objects additionally require an expired domain retention deadline, so
perception/artifact retention is never shortened. Legacy paths and modern object
keys are validated as a whole batch before unlinking. Interrupted filesystem/DB operations
can leave missing-byte rows or unindexed files: keep disk alarms, inspect against
the ledger and restore rather than running a blind recursive deletion. This is
not account erasure. `account_lifecycle.py` implements owner-qualified transactional
account erasure including private objects and configured avatars; see
`docs/identity-tenancy-data-lifecycle.md`. Legal retention policy, backup expiry
and provider-side erasure still require operator approval; do not advertise
GDPR/App Store compliance from this maintenance command alone. Removed committed legacy SQLite/log artifacts remain
in Git history; rotate any formerly exposed authority, do not assume deletion
purged history.

## App Store operational release gate

Read the Expo SDK 57 documentation and `frontend/scripts/friend-beta-release-preflight.mjs`.
Existing `eas.json` production profile is store distribution, remote monotonic
build numbers and auto-increment on a committed tree. Bundle remains
`io.magistrate.cockpit`; Apple sign-in/secure-store/notification plugins and the
limited background mode remain unchanged. No signing identities are invented.

Production app config/preflight require a real HTTPS Gateway, approved
`APPLE_TEAM_ID`, public privacy/support URLs and the actual App Store Connect
`ascAppId`. URL syntax validation is **not** proof that pages are published.
ATS stays enforced. Remote JavaScript updates are explicitly disabled; rollback
is a newly reviewed store build with a higher build number, not an unsigned OTA.
Keep the existing export-compliance declaration only after the operator verifies
native dependency/encryption usage; server-side encryption is not an iOS claim.

Before submission: verify resolved Expo config, generated entitlements and SDK
privacy manifests, declared permission purposes, provider redirects/audiences,
real privacy/support/deletion pages, APNs credentials, physical-device login,
refresh replay/logout, microphone/push denial, account deletion and App Review
demo access. Publish truthful App Store privacy labels (account identity, user
content/voice, diagnostics and third-party processing as actually deployed).
Then use the reviewed EAS production build ID for submission—never `latest` from
another lane. This work does not include a signed IPA, TestFlight acceptance,
store screenshots, a privacy-law assessment or an Apple approval.

## Dependency audit — 2026-09-28

`npm audit fix --package-lock-only --ignore-scripts` applied compatible transitive
fixes. Puppeteer Core was updated to 25.12.0 (Node >=22.12), removing the vulnerable
archive-extraction chain. Full frontend tests/typecheck/export passed afterwards.
A scoped `xcode -> uuid@11.1.1` override preserves the CommonJS `v4()` interface;
a regression test generates 100 valid unique Xcode IDs and verifies the resolved
version. Release tests, resolved Expo config and web export passed after this fix.
Audit count fell from **23 (8 high)** to **4 (1 high, 3 moderate)**; this is not a
clean bill of health. Remaining root advisories:

- Metro's `image-size@1.2.1`: high, GHSA-5p2g-fcmc-qvqq and GHSA-w3rx-r6r6-pgpr.
  Asset parsing/build-time exposure; do not process hostile repositories/assets
  with privileged build credentials. The supported RN 0.86.3 stack resolves Metro
  0.84.4, which declares `image-size ^1.0.2`; registry 1.x ends at vulnerable 1.2.1.
  Metro `src/Assets.js:getAssetData` calls synchronous `imageSize(filePath)` (string)
  for non-ZIP assets. Patched 2.0.4's default accepts byte arrays only and its
  filesystem API is a separate asynchronous `fromFile` export. A blanket 2.x
  override therefore breaks ordinary native/web asset builds. Remediation requires
  a reviewed Metro compatibility patch/replacement or coordinated supported
  React Native/Expo upgrade, not just lockfile regeneration. No custom parser fork
  or unsupported Metro 0.87 major was substituted during this security lane.
- Expo Router's `decode-uri-component`: moderate, GHSA-vcc3-ghjq-m6fr.
- Xcode tooling's `uuid` moderate GHSA-w5hq-g745-h8pq and its propagated Expo
  findings are **remediated** by the scoped override above, not waived.

A production release must resolve these through compatible upstream patches or
an explicitly reviewed, time-bounded risk disposition with reachability evidence.
Do not use `npm audit fix --force`'s suggested Expo downgrade. Run current npm and
Python dependency audits again at release time; this report is point-in-time and
no Python vulnerability scan is claimed. Keep npm/uv lockfiles, `npm ci`,
`uv sync --frozen`, pinned producer verification and normal CI. Dependency
installation and CI/build credentials must be isolated from untrusted repo code.

## Validation in this lane

- Rebased Gateway: **459 passed**, including the real installed/verified pinned
  producer contract. Suites retain hosted execution, billing, routed models,
  private objects, context and tenant lifecycle.
- The exact concurrent PostgreSQL smoke plus two-tenant isolation/erasure scripts
  passed against a task-owned PostgreSQL 16 container. Additional read-only
  readiness/metrics probes passed; no shared database was changed.
- Full frontend `npm test` (browser suites included), typegen/typecheck, lint
  (15 existing warnings, no errors), and web export passed after rebasing.
  Repeated npm audit still reports four findings (one high, three moderate).
- Deployment workflow, real-preflight/SQLite and PostgreSQL snapshot-acknowledgement
  safeguards, non-OpenAI routing, billing fail-closed and restricted beta smoke
  contracts passed against command stubs, not a live host. The SQLite test proves
  the recovery point precedes all schema migration.
- Focused security/storage tests cover received-byte limits, concurrency/timeouts,
  two-principal attacks, storage quotas, path tampering, log redaction, operator
  metrics access, URL confusion, provider redirects/oversize responses and restore.

No live deployment, real-provider request, worker lifecycle operation, store
submission or production retention deletion was performed.
