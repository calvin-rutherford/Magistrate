# Production activation — owner runbook

Authority: [production matrix](PRODUCTION_STATUS.md). **Activation verdict:
BLOCKED_EXTERNAL.** Repository passes do not authorize spending, deployment,
worker lifecycle changes, legal representations, pricing or publication. No live
provider/payment/deployment, EAS build, TestFlight or physical iPhone was exercised
by this reconciliation. Owners execute the following against the exact candidate.

## 1. Release worksheet and admission

Record in restricted release storage, never substitute invented identifiers:

| Record | Exact source | Evidence to retain |
|---|---|---|
| Candidate | Clean merged Git checkout and forge checks | Full SHA, workflow URLs/results, schema export digest, locked dependency scans |
| Origins | DNS/hosting owners | Actual HTTPS application and Gateway origins, certificate names |
| Accounts | Signed-in provider console | Actual account/org/project/team IDs, legal entity and named accountable owner |
| Generated IDs | Console creation/output | Real client/App/installation/product/price/webhook/build IDs copied exactly |
| Secrets | Approved secret manager | Secret reference/version/access/rotation owner, never secret bytes |
| Verification | Candidate live/device run | Time, build/SHA/device/network, redacted result, artifact hash and named reviewer |

`APP_ORIGIN`, `GATEWAY_ORIGIN` and `REVERSED_IOS_CLIENT_ID` below are worksheet
labels, **not additional environment variables**. Resolve them to actual values;
never deploy literal placeholders. Prefer same-origin frontend/Gateway; web
refresh cookies require a same-site deployment. The actual client variable
`EXPO_PUBLIC_GATEWAY_URL` is the Gateway origin followed by `/api/v1`.

Run the commands in [PRODUCTION_STATUS.md](PRODUCTION_STATUS.md). A clean hermetic
receipt and the protocol export bind to the candidate SHA/registry digest.
Generate the release packet, attach per-checkpoint hashed redacted artifacts and
run `production_acceptance.py verify`. All external checks start BLOCKED_EXTERNAL;
only a named owner's actual reviewed observations can complete them. The verifier
does not authenticate the truth of an attestation. Protect artifacts from customer
content, bearer tokens, keys and private decision answers.

## 2. Apple identity

**Owner:** actual legal entity's Apple Developer Account Holder/Admin.

1. `developer.apple.com/account` → Certificates, Identifiers & Profiles →
   Identifiers: confirm **App ID `io.magistrate.cockpit`**, intended Team and
   Sign in with Apple plus push capabilities. This committed bundle ID is not
   proof of registration or an entitlement in a signed binary.
2. Create/confirm a web **Services ID**, associate its primary App ID, enter the
   actual frontend domain and exact return **`APP_ORIGIN/`** (trailing slash).
   `/api/v1/auth/apple/callback` is not this client's login callback.
3. Keys → Sign in with Apple key for that App ID: record real Team ID/Key ID,
   escrow the downloaded `.p8` privately and record its secret version.
4. Gateway: `MAGISTRATE_APPLE_CLIENT_IDS=io.magistrate.cockpit`,
   `MAGISTRATE_APPLE_SERVICE_ID`, `MAGISTRATE_APPLE_TEAM_ID`,
   `MAGISTRATE_APPLE_KEY_ID`, `MAGISTRATE_APPLE_PRIVATE_KEY` (escaped newlines
   supported), and exact web return in `MAGISTRATE_AUTH_REDIRECT_URIS`.
5. Client: `EXPO_PUBLIC_APPLE_SERVICE_ID` equals the console Services ID.
   Native uses the bundle audience/entitlement, not a web callback. Rebuild after
   changing bundle/entitlement configuration.
6. Verify real native and web fresh/repeat login, withheld name/email, cancellation,
   issuer/audience/nonce/state, rotation, family revocation and account recovery.
   Subjects never auto-link by email; signed synthetic assertions are not evidence.

## 3. Google identity

**Owner:** actual Google Cloud project and OAuth branding/consent administrator.

1. `console.cloud.google.com` → intended project → Google Auth Platform (or
   APIs & Services → OAuth consent screen/Credentials). Record Project ID and
   legal app name, support/developer contacts, authorized domains, privacy/terms,
   audience/test users and publishing/verification state.
2. Web application client: JavaScript origin **`APP_ORIGIN`**, authorized redirect
   **`APP_ORIGIN/`**. Record the generated client ID. Web login submits a verified
   ID-token assertion; there is no invented web-client-secret variable.
3. iOS OAuth client: bundle **`io.magistrate.cockpit`**, actual Team and Store ID
   where requested. Record generated client ID and its reversed scheme. The exact
   native redirect is **`REVERSED_IOS_CLIENT_ID:/oauthredirect`** (one slash after
   colon). Do not use the web audience for the native refresh channel.
4. Gateway: `MAGISTRATE_GOOGLE_WEB_CLIENT_IDS`,
   `MAGISTRATE_GOOGLE_IOS_CLIENT_IDS`, exact comma-separated
   `MAGISTRATE_AUTH_REDIRECT_URIS` including web and native redirects.
   `MAGISTRATE_GOOGLE_CLIENT_IDS` is only an optional extra audience list.
5. Client: `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID`, `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID`,
   `EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID`; the latter two must match and are
   required together. Rebuild after scheme changes.
6. Verify native PKCE, web cookie-only refresh, native SecureStore refresh,
   wrong-audience/redirect refusal, nonce replay, logout/reuse/revocation and
   stable principal. Android provider login is not claimed by iPhone/web proof.

Optional Gateway controls: `MAGISTRATE_PROVIDER_REFRESH_TTL_SECONDS` (one hour
through 90 days) and `MAGISTRATE_PROVIDER_SESSION_SCOPES`; human scopes never
include producer `response`. Frontend onboarding must resume welcome/name/GitHub/
signed subscription from canonical rows, not callback query success flags.

## 4. GitHub identity, repository App and worker broker

**Owner:** intended GitHub organization owner/App manager and worker security owner.
These are distinct boundaries; repository access never falls back to `gh-axi`.
Use **gh-axi** for operator forge operations, not as customer authorization.

**OAuth identity for onboarding:** GitHub → Settings → Developer settings →
OAuth Apps (or organization OAuth Apps). Record actual owner/client ID/secret
reference. Homepage is `APP_ORIGIN`; exact callback is
**`GATEWAY_ORIGIN/api/v1/auth/github/callback`**. Configure
`GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`,
`MAGISTRATE_OAUTH_CALLBACK_BASE_URL` (Gateway origin) and exact
`MAGISTRATE_OAUTH_REDIRECT_URIS` including `magistrate://account` and the selected
web return. Requested scopes are **`read:user`, `user:email`**, not `repo`.
This allowlist is separate from Apple/Google `MAGISTRATE_AUTH_REDIRECT_URIS`.

**Repository GitHub App:** organization Developer settings → GitHub Apps →
create/confirm the service App. Record numeric App ID, actual slug, owning org,
private-key version and random webhook secret (at least 32 characters).

| Console field | Exact implemented contract |
|---|---|
| Setup URL | `GATEWAY_ORIGIN/api/v1/github/app/callback` |
| Redirect on update | Enabled |
| Webhook URL / active | `GATEWAY_ORIGIN/api/v1/github/webhooks` / enabled |
| Read-only product permissions | Contents read, Pull requests read, Checks read, implicit Metadata read; no organization permissions |
| Events | Installation, Installation repositories, Repository, Pull request, Check run, Check suite |
| Installation | Approved account visibility; choose Only select repositories unless the owner explicitly approves all |

Gateway configuration: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, either
`GITHUB_APP_PRIVATE_KEY_PATH` (private service-owned PEM, mode 0600) **or**
`GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_WEBHOOK_SECRET`,
`MAGISTRATE_GITHUB_APP_CALLBACK_BASE_URL`,
`MAGISTRATE_GITHUB_APP_REDIRECT_URIS` (exact `magistrate://prs` and actual web
`APP_ORIGIN/prs`). Partial/insecure configuration fails startup. Installation
uses principal-bound one-use state, server-side App verification and selected
repositories. Record the real installation/provider repository IDs; opaque project
bindings alone confer no authority. Explicit reconciliation repairs webhook drift.

**Hosted write broker:** the read-only App setup alone cannot issue write tokens.
If objectives need commits/PR creation, the GitHub/security owner must approve
Contents/Pull requests write on the **same mapped App installation** and the
broker's configured `MAGISTRATE_GITHUB_PERMISSIONS`. Gateway product read tokens
remain narrowed to read permissions even when the App has approved broader
permissions. Do not invent a separate installation mapping or mount the App key
in a worker. Record the broker account/App credential reference and its mTLS
identity; it must return only exact mapped-repository, requested-permission,
short-lived tokens. No broker service is bundled/provisioned by this repository.

Verify real private allowed-repository read, denied unselected/foreign repository,
rename/removal/suspend/uninstall, signed webhook replay/conflict, expired OAuth
state, broker scope/expiry/tenant denials and full forge artifact URL from a real
bounded objective. See [GitHub component contract](github-app-activation.md).

## 5. Stripe, catalog and Credits

**Owner:** legal merchant account owner and billing administrator; not the agent.

Stripe Dashboard → confirm actual account/legal business/country, payout/tax
settings and test/live mode; record account ID. Product catalog → approved
Individual/Pro recurring products/prices and Credit Pack one-time price/currency;
record actual product and `price_...` IDs **separately by mode**. Copy
`gateway/billing_catalog.json` to a private service-owned absolute file outside
checkout, mode 0600; insert actual prices and review grants/rates/margins/limits.

Configure `MAGISTRATE_BILLING_CATALOG_PATH`, `STRIPE_SECRET_KEY` (approved restricted
Customer/Checkout/Billing Portal/Subscription/Invoice access),
`STRIPE_WEBHOOK_SECRET`, and `MAGISTRATE_BILLING_RETURN_ORIGINS` (exact HTTPS
app origin and approved native `magistrate://chat`). API key and webhook signing
secret are different. Do not activate legacy `MAGISTRATE_STRIPE_*` single-price
compatibility for a new deployment.

Workbench → event destination: **`GATEWAY_ORIGIN/api/v1/billing/webhooks/stripe`**.
Record endpoint ID/signing-secret version and subscribe to:
`checkout.session.completed`, `customer.subscription.created`,
`customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`,
`invoice.payment_failed`. Configure/activate Customer Portal, cancellation timing,
payment methods and approved return locations. Checkout URLs are server-generated;
browser return never grants subscription/credit. Catalog must report active
activation and checkout availability only after real configuration.

First use approved **test mode**: subscription/renewal/failure/grace/cancellation,
Credit Pack, duplicate/out-of-order/mutated/invalid-signature events, concurrent
reservations, insufficient budget (no worker publication), measured settlement,
reviewed refund and ledger reconciliation against actual Stripe event IDs. Capture
owner review for live key/price/webhook promotion as one change. General disputes,
taxes and mobile purchase-policy approval are not automatically implemented.
See [BILLING_MODEL.md](BILLING_MODEL.md).

## 6. Gateway, DB, storage, secret escrow

**Owner:** hosting/database/storage administrator and security key custodian.
Deploy only `gateway/app/main.py` and exported Expo web. Legacy Django/Compose,
Pi/AR/rsync/launch paths are not production or rollback entrypoints.

| Setting | Source and required meaning |
|---|---|
| `MAGISTRATE_ENV` | `production` |
| `MAGISTRATE_BOOTSTRAP_SECRET` / `MAGISTRATE_BOOTSTRAP_USER_ID` | Independently generated >=32-character operator secret and approved principal; never a customer login |
| `MAGISTRATE_SECRET_KEY` | Generated Fernet key in secret manager; versioned escrow for restore |
| `MAGISTRATE_CORS_ORIGINS` | Exact HTTPS frontend origins, no wildcard/path |
| `MAGISTRATE_DIST_DIR` | Actual exported frontend directory |
| `MAGISTRATE_DEV_AUTO_SESSION` | False/unset; obsolete Chat/Pi flags removed |
| `MAGISTRATE_SESSION_TTL_SECONDS` / `MAGISTRATE_SESSION_SCOPES` | Reviewed short bearer lifetime and least authority |
| `MAGISTRATE_FRIEND_BETA_ENABLED` | False unless a named restricted cohort is approved |
| `MAGISTRATE_DB_PATH` | Absolute private external SQLite path, **single instance only** |
| `MAGISTRATE_DATABASE_URL` / `MAGISTRATE_STATE_DIR` | Alternative PostgreSQL TLS/least-privilege DSN plus private shared POSIX state volume at same path on every instance |
| `MAGISTRATE_POSTGRES_BACKUP_CONFIRMED` | `true` only after a restorable snapshot for this candidate is verified; not a substitute for a drill |
| `MAGISTRATE_OBJECT_STORAGE_DIR` | Private object root; `MAGISTRATE_CHAT_UPLOAD_DIR` is legacy alias |
| `MAGISTRATE_AVATAR_DIR` | Separate bounded image-only public avatar directory; never overlap private objects |
| `MAGISTRATE_UPLOAD_SIGNING_KEY` | Independent persistent signing secret (otherwise Fernet secret fallback); review access-URL rotation |
| `MAGISTRATE_UPLOAD_SCAN_COMMAND` | Optional trusted no-shell scanner argv; actual deployed scan/rejection/unavailable proof required if enabled |
| `MAGISTRATE_UNATTACHED_UPLOAD_TTL_SECONDS` / `MAGISTRATE_ATTACHED_UPLOAD_TTL_SECONDS` | Approved retention, respecting domain/perception expiry |
| `MAGISTRATE_UPLOAD_USER_BYTES` / `MAGISTRATE_UPLOAD_USER_COUNT` / `MAGISTRATE_UPLOAD_GLOBAL_BYTES` / `MAGISTRATE_UPLOAD_GLOBAL_COUNT` | Reviewed private-storage quotas |
| `MAGISTRATE_MAX_INFLIGHT_REQUESTS` | Per-process admission limit, in addition to edge limits |

Choose exactly one DB backend. SQLite/env files are service-owned mode 0600;
state/backup parents mode 0700 outside checkout. PostgreSQL owner records actual
console/project/cluster/DB/role/region/CA/PITR policy, verifies TLS (e.g.
`sslmode=verify-full`) and isolated restore. The upload adapter is **POSIX**, not
S3: provision an encrypted private shared volume for multi-instance operation,
record filesystem/volume ID, backup/lifecycle/capacity/disk quota and mount policy.
No bucket SDK or cloud-key environment variable activates an unimplemented store.

Retain required previous Fernet keys via the reviewed
`MAGISTRATE_SECRET_KEY_VERSION`, `MAGISTRATE_PREVIOUS_SECRET_KEY`,
`MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION`, `MAGISTRATE_KEY_ROTATION_ENABLED`
procedure, not automatic startup rewriting. Back up DB **before migration**, files,
exact revision and secret versions as one recovery inventory. SQLite operator
entrypoints are `gateway/scripts/storage_ops.py backup SOURCE NEW_DESTINATION`
and `restore SNAPSHOT NEW_DESTINATION`; PostgreSQL uses managed backups/PITR.
Never overwrite a live DB, hand-edit migration rows, or blindly copy WAL files.
Compare all owner/credit/context/execution/receipt rows and object digests, test
revocation/keys/authenticated smoke and record recovery duration. An old snapshot
can resurrect authority: keep traffic closed until revocation review/reissue.

## 7. Hosted workers and model providers

**Runtime owner:** isolated execution service operator, image/registry owner and
GitHub broker security owner. Activation is separate from running this repository's
tests. No release/read action may drive shared Herdr lifecycle.

For public SaaS configure all implemented hosted names:
`MAGISTRATE_HOSTED_EXECUTION_ENABLED=true`, `MAGISTRATE_WORKER_IMAGE` (immutable
`@sha256:` digest), `MAGISTRATE_WORKER_GATEWAY_URL`,
`MAGISTRATE_ISOLATION_BACKEND_URL`, `MAGISTRATE_GITHUB_TOKEN_BROKER_URL`,
`MAGISTRATE_ISOLATION_CLIENT_CERT`, `MAGISTRATE_ISOLATION_CLIENT_KEY`,
`MAGISTRATE_ISOLATION_CA`, `MAGISTRATE_WORKER_IDENTITY_KEY` (>=32 random bytes),
`MAGISTRATE_WORKER_NETWORK_HOSTS` (exact approved hosts including Gateway),
`MAGISTRATE_GITHUB_PERMISSIONS`, `MAGISTRATE_WORKER_MAX_GLOBAL`,
`MAGISTRATE_WORKER_MAX_PER_TENANT`, `MAGISTRATE_WORKER_CPU_MILLIS`,
`MAGISTRATE_WORKER_MEMORY_MIB`, `MAGISTRATE_WORKER_WORKSPACE_MIB`,
`MAGISTRATE_WORKER_DEADLINE_SECONDS`, `MAGISTRATE_WORKER_CLEANUP_SECONDS`,
`MAGISTRATE_WORKER_POLL_SECONDS`. Use HTTPS service origins and private mTLS files.

Record actual cloud/backend account/project/service identity, digest-pinned image
and `/opt/magistrate/bin/firstmate-worker` implementation, certificate issuance/
rotation/revocation and policy IDs. The provider-neutral API and mandatory controls
are in [hosted-execution.md](hosted-execution.md). Prove hostile two-tenant process,
filesystem, credential, network/metadata, CPU/memory/process/workspace/wall-time
boundaries; no ingress, default-deny egress and bounded cleanup; crash recovery
without duplicate objective/evidence; exact scoped broker receipts. Repository
mocks and `activation: not-observed` health cannot pass this gate.

Restricted local operation instead uses `FM_HOME`, `MAGISTRATE_FIRSTMATE_ROOT`,
`MAGISTRATE_FIRSTMATE_TOOL_PATH`, `MAGISTRATE_FIRSTMATE_RUNTIME_HOME`,
`MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED` with the exact
`runtime/firstmate-producer.lock.json` pin. Install/verify code is not activation
or isolation. Do not enable local fallback for an incomplete hosted configuration.

**Model owners:** actual OpenAI project (`platform.openai.com`), Anthropic
organization/workspace (`console.anthropic.com`) and/or Google AI Studio/Cloud
project (`aistudio.google.com`). Record approved provider/model/account IDs,
quotas/spend limits, data terms and secret references. Default credential names
are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`; provider catalog entries
may name other reviewed server-only credential envs. Review the full typed
`MAGISTRATE_MODEL_ROUTING_CONFIG` override/default capabilities/prices/budgets/
fallback limits and `MAGISTRATE_PROVIDER_ALLOWED_HOSTS`. Retired
`MAGISTRATE_MAGI_MODEL*` selectors are not routing controls. Speech uses
`VOICE_STT_PROVIDER`, `VOICE_STT_MODEL`, `OPENAI_BASE_URL` independently.
`MAGISTRATE_EXECUTION_INVENTORY` is verified harness metadata, not activation.

Verify bounded real native-provider tool/direct turns, usage versus estimates,
budget refusal/unknown usage, allowed fallback and no replay after a tool; exercise
actual provider and harness replacement with preserved context/evidence. No client
gets provider/runner credentials. A configured flag is not a live health probe.

## 8. DNS/TLS, deployment, monitoring

**Owner:** registrar/DNS/hosting administrator and named on-call. Record zone,
ingress/service identities, actual A/AAAA/CNAME targets, certificates/renewal,
HTTPS/WSS `/api/v1/events`, non-shared caching of owner data, same-site cookies,
body/rate/connection limits and private worker/DB/metrics network policy. Verify
from outside the host, not just loopback. An unauthenticated 401 is not readiness.

Review `runtime/magistrate-gateway.service.example`,
`runtime/nginx-production.conf.example`, `gateway/logging.json`. Run as a dedicated
non-root user, Gateway on loopback, Uvicorn `--no-access-log --log-config logging.json`.
The Nginx template avoids query/URL secrets and blocks `/internal/` publicly.
Only `/readyz` HTTP **200** passes deployment; it checks persisted schema and
configured model routing, not live providers/workers.

Manual-only Actions deployment uses repository secrets `MAGISTRATE_DEPLOY_HOST`,
`MAGISTRATE_DEPLOY_USER`, `MAGISTRATE_DEPLOY_SSH_KEY`,
`MAGISTRATE_DEPLOY_KNOWN_HOSTS` and optional variable `MAGISTRATE_DEPLOY_PORT`.
Never put bootstrap/provider keys in Actions. Authorized deployment uses
`scripts/deploy_magistrate.sh` guards; do not dispatch merely to gather evidence.
It is update-only, not atomic blue/green or automatic rollback. Trusted-host smoke
is `scripts/smoke_magistrate.sh`. Restricted-beta smoke uses
`MAGISTRATE_FRIEND_BETA_GATEWAY_URL`, private `MAGISTRATE_FRIEND_BETA_ACCESS_FILE`
and `MAGISTRATE_SMOKE_PYTHON`; use a dedicated grant since the smoke retires it.
No paid model request or physical proof is implied by those smoke scripts.

Monitoring: choose the actual Prometheus/compatible project and alert destination;
record console/account/project/on-call IDs. Set independent
`MAGISTRATE_METRICS_TOKEN`, scrape private `/internal/metrics` with secret-file
bearer authority, review/load `runtime/prometheus-alerts.yml` and configure/test
alerts for readiness, DB/disk/inodes, backup freshness, provider/queue/webhook/
receipt failures and redacted operational errors. No arbitrary remote DSN env
is implemented; `set_error_reporter()` is a trusted sanitized in-process seam.
Capture actual alert delivery, retention/redaction and incident drill, not only
configuration screenshots. Forge rulesets must require all merged CI checks and
release approvals; repository files cannot prove console protections exist.

## 9. Expo, push, EAS, TestFlight and App Store

**Owner:** actual Expo organization administrator and Apple signing/release owner.
Committed IDs, not verified console access: owner **`melkezics-team`**, project
**`aedf2c07-8f71-4e31-9e6e-7968f30479b1`**, slug **`magistrate`**, bundle/package
**`io.magistrate.cockpit`**.

1. `expo.dev` → organization/project → Environments: set matching development,
   preview and production `EXPO_PUBLIC_GATEWAY_URL`, public provider client IDs,
   `EXPO_PUBLIC_PRIVACY_URL`, `EXPO_PUBLIC_SUPPORT_URL` and actual `APPLE_TEAM_ID`.
   Optional `EXPO_OWNER`/`EXPO_PUBLIC_EAS_PROJECT_ID` overrides must match intended
   committed linkage. Privacy/support must be **published**, not merely valid URL
   syntax. Terms/deletion/support contacts also require real owner-approved pages.
2. Apple Developer → APNs key: record Team/Key IDs and `.p8` reference; EAS
   Credentials → correct team/bundle/profiles/push key. Android additionally needs
   real Firebase project and FCM V1 service-account credential/config and separate
   device proof. No APNs/FCM key goes into `EXPO_PUBLIC_*`.
3. Gateway defaults to Expo send/getReceipts. Optional operator endpoint overrides
   are `MAGISTRATE_EXPO_PUSH_URL` and `MAGISTRATE_EXPO_PUSH_RECEIPTS_URL`;
   `MAGISTRATE_NOTIFICATION_POLL_SECONDS` controls the existing notification loop.
   They are not signing credentials. Verify real registration, ticket then receipt,
   invalid-token retirement, unread fallback and actual cold/warm tap. A receipt
   is provider handoff, not physical arrival. Open-tab web fallback is not native
   or service-worker background push.
4. `appstoreconnect.apple.com` → Apps → actual legal entity's app for the bundle,
   owner-chosen name/SKU. Copy its real numeric Apple app ID to
   `frontend/eas.json` → `submit.production.ios.ascAppId` (currently absent).
   Record Team ID and, if used, App Store Connect API Key ID/Issuer ID/private-key
   reference through EAS submission credentials. No `${...}` placeholder works
   for `ascAppId`.
5. On the reviewed clean candidate with its production environment loaded:

   ```sh
   cd frontend
   npm ci
   npm run beta:preflight -- --profile production
   npx eas-cli@23.0.0 build --platform ios --profile production
   npx eas-cli@23.0.0 submit --platform ios --profile production --id ACTUAL_REVIEWED_EAS_BUILD_ID
   ```

   Replace the last label with the actual approved build ID. These are **owner
   actions, not executed here**. Production is store distribution, committed tree,
   remote monotonic build number and auto-increment. Capture EAS ID/SHA/app/build
   numbers, archive hash, entitlements/privacy manifests and secret scan. ATS stays
   enforced and OTA disabled; rollback is a reviewed higher-numbered store build.
6. App Store Connect → processing/export compliance, TestFlight groups and external
   review as required. Install that exact build on recorded physical iPhone/iOS/
   Wi-Fi/cellular combinations and run Spencer plus [DEAT-001](DEAT-001.md).
   Internal/ad-hoc builds, simulator/Expo Go or build success are not this evidence.
7. Legal/product owner approves actual seller/entity/contact, public privacy/terms/
   support/deletion URLs, data labels/tracking/subprocessors/consent/retention,
   age/content rating, screenshots/review notes and a least-privilege review
   account. Review Apple's digital-goods/payment rules against the actual Stripe
   UX and native dependency encryption/export declarations. No approval, URL,
   price or legal identity is invented by this runbook.

## 10. Mandatory Spencer and moat

`python3 scripts/production_acceptance.py list` is the exact checkpoint registry.
Spencer must be genuinely new: no SSH/bootstrap/runner configuration or preseeded
profile. In order: real login → welcome/name → GitHub OAuth and App/repository/
project → approved test-mode subscription/budget → real objective/evidence →
confirmed Attention answer → upload/voice/permission denial → offline/reopen/
second device → push ticket/receipt/cold-warm tap → logout/revoke/deletion.

Seven independent moat records: provider replacement, harness replacement,
context continuity, cost routing, client-independent background work, Attention
loop and device independence. Use approved isolated failure/recovery exercises;
never infer autonomy from model prose or hermetic stub events. Preserve failed
attempts, fix then rerun, and attach exact-candidate hashes with a named reviewer.
Dependency/security review includes fresh CI scans and the reviewed compatibility
bridges in [SECURITY_MODEL.md](SECURITY_MODEL.md). Only genuinely external account,
service, legal and physical evidence remains BLOCKED_EXTERNAL; no final production
or App Store acceptance claim is allowed until every checkpoint is COMPLETE.
