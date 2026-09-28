# Production activation — owner runbook

Authority: [production matrix](PRODUCTION_STATUS.md). **Activation verdict:
BLOCKED_EXTERNAL.** Repository implementation gaps remain FAILED independently.
This runbook does not authorize spending, a deployment, worker lifecycle changes,
legal representations, pricing, or publication. The named account owner performs
those actions after the corresponding implementation gates pass.

## 1. Freeze the inputs, never invent the values

Choose the real production account owners and record the following in restricted
release storage. A12 has not observed these consoles for this candidate.

| Record | Source of truth | Required recorded value |
|---|---|---|
| Candidate | Clean Git checkout and CI | Full merged commit SHA, workflow run URLs/checks, exported protocol hash |
| App and API origins | DNS owner and deployment owner | Exact HTTPS frontend origin and Gateway origin; no placeholder host |
| Accounts | Provider console signed-in account/organization | Account/team/project identifiers and named operator, not credentials |
| Generated IDs | Console after creation | App/client/installation/product/price/webhook/build IDs copied exactly |
| Secrets | Approved secret manager | Secret reference, version, access/rotation owner; never plaintext in packet |
| Verification | Candidate live run | Time, platform, public endpoint, redacted result, artifact hash, operator |

In the formulas below, `APP_ORIGIN` and `GATEWAY_ORIGIN` are worksheet labels,
**not implemented environment variables**. Replace them with the actual origins;
never deploy literal placeholders. Use a same-origin frontend/Gateway if possible;
web provider refresh cookies need a same-site deployment. `EXPO_PUBLIC_GATEWAY_URL`
is the actual Gateway HTTPS origin followed by `/api/v1`.

Only currently implemented environment names are listed as configuration. An
absent Stripe/GitHub-App/storage/monitoring contract is explicitly absent, not a
made-up variable that supposedly activates a feature. After those owners merge,
A12 must replace each absent-contract entry with the exact code-defined settings
and route before production acceptance.

## 2. Apple identity and signing

**Owner:** Apple Developer Account Holder/Admin for the actual legal entity.

1. `developer.apple.com/account` → Certificates, Identifiers & Profiles →
   Identifiers: confirm the App ID `io.magistrate.cockpit` belongs to the intended
   Team and enable **Sign in with Apple** and required push capability. This
   bundle identifier is committed, not proof of registration or entitlement.
2. Create/confirm a **Services ID** for web login. Configure Sign in with Apple
   against the correct primary App ID. Enter the actual frontend domain and
   exact return URL `APP_ORIGIN/` (including trailing slash). Do not substitute
   `/api/v1/auth/apple/callback`: that is not this client's login route.
3. Keys → create/confirm a Sign in with Apple key for the primary App ID. Record
   the real Team ID and Key ID; download the `.p8` into the secret manager (Apple
   download availability is limited). Do not commit or print it.
4. Configure Gateway names:
   `MAGISTRATE_APPLE_CLIENT_IDS=io.magistrate.cockpit`,
   `MAGISTRATE_APPLE_SERVICE_ID`, `MAGISTRATE_APPLE_TEAM_ID`,
   `MAGISTRATE_APPLE_KEY_ID`, `MAGISTRATE_APPLE_PRIVATE_KEY`.
   The service/key/team values come from the console. The private-key value
   supports escaped newlines. Configure the exact web return URL in
   `MAGISTRATE_AUTH_REDIRECT_URIS`.
5. Configure public client `EXPO_PUBLIC_APPLE_SERVICE_ID` with that same Services
   ID. Native Apple uses the bundle audience and native entitlement, not a web
   return URL. `usesAppleSignIn` and the Expo Apple plugin are committed.
6. Verify real native and web login on the candidate: nonce/state, issuer,
   audience, stable provider subject, repeat login with withheld email/name,
   cancellation, rotation and revoked family refusal. Repository signed-fixture
   tests are not this verification. Never auto-link accounts by email.

## 3. Google identity

**Owner:** Google Cloud project owner and OAuth consent/branding administrator.

1. `console.cloud.google.com` → select the actual project → Google Auth Platform
   (or APIs & Services → OAuth consent screen/Credentials): set the legal app
   name, support/developer contacts, authorized domains, privacy/terms URLs,
   audience/test users and publishing/verification status. Record Project ID.
2. Create/confirm a **Web application** OAuth client. Authorized JavaScript
   origin is `APP_ORIGIN`; exact authorized redirect is `APP_ORIGIN/`. Copy the
   generated client ID. Current web login uses an ID-token assertion verified
   at Gateway, not an invented Google web client-secret setting.
3. Create/confirm the **iOS** OAuth client with bundle `io.magistrate.cockpit`,
   the actual Team ID and App Store ID if requested. Copy its generated client
   ID and reversed client-ID scheme. The actual native callback is
   `REVERSED_IOS_CLIENT_ID:/oauthredirect` (one slash after the colon), as built
   by `ProviderSignIn.ts`. Do not use the web client as the native audience.
4. Gateway: `MAGISTRATE_GOOGLE_WEB_CLIENT_IDS`,
   `MAGISTRATE_GOOGLE_IOS_CLIENT_IDS`, and exact comma-separated
   `MAGISTRATE_AUTH_REDIRECT_URIS` containing web and native callbacks.
   `MAGISTRATE_GOOGLE_CLIENT_IDS` is an optional additional-audience list, not a
   replacement for platform-specific production configuration.
5. Expo: `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID`,
   `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID`,
   `EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID`. Rebuild after URL scheme changes.
6. Verify actual native PKCE exchange and web login, wrong-audience/redirect
   refusal, stable principal, nonce replay refusal, cookie-only web refresh,
   SecureStore native refresh, logout and revocation. Android Google sign-in is
   not claimed by the current iPhone/web client; do not submit Android as tested.

Optional server controls: `MAGISTRATE_PROVIDER_REFRESH_TTL_SECONDS` (3600 through
7776000) and `MAGISTRATE_PROVIDER_SESSION_SCOPES`. Never issue producer `response`
scope to a human login. Provider scopes do not themselves create tenant isolation.

## 4. GitHub

**Owner:** Intended GitHub organization owner/app manager; A3 owns implementation.

**Existing OAuth path:** GitHub Settings → Developer settings → OAuth Apps
(or the organization's OAuth Apps) → the approved app. Record real app owner,
client ID and secret reference. Homepage is the actual app origin. Authorization
callback is exactly `GATEWAY_ORIGIN/api/v1/auth/github/callback`.
Set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`,
`MAGISTRATE_OAUTH_CALLBACK_BASE_URL` to the Gateway origin and
`MAGISTRATE_OAUTH_REDIRECT_URIS` to the exact app-return allowlist (native
`magistrate://account` and the actual selected web callback). This OAuth allowlist
is **different** from `MAGISTRATE_AUTH_REDIRECT_URIS` used for Apple/Google login.
Current requested scopes are `repo`, `read:org`, `user:email`; review their breadth.

`MAGISTRATE_GITHUB_REPO` and optional `GH_AXI_BIN` select the existing server
`gh-axi` PR read seam. Its service-account authentication is not evidence that an
end user's OAuth account can access that repository. Do not use this shared
service identity as SaaS repository authorization.

**Required A3 convergence:** a tenant installation/repository authorization
contract. If A3 uses a GitHub App, its owner must create it under Developer
settings → GitHub Apps, register the **merged code's exact** setup/callback/webhook
URLs, least required repository permissions/events, installation selection and
webhook secret; capture App ID, Client ID, private-key secret reference and
installation ID. Those route/env names are absent in this baseline. Creating a
console app cannot fix that repository FAILED item. Never invent `/webhooks/github`
or a secret name and call it wired.

Verification: private allowed repository read, denial for unselected repository
and another tenant, installation/account revoke, OAuth state replay refusal,
webhook signature/replay tests once implemented, and a real objective's forge
artifact. Use `gh-axi` for operator GitHub operations; never merge as part of smoke.

## 5. Stripe and ledger

**Owner:** Legal merchant account owner and billing administrator; A4 owns code.

Stripe Dashboard (`dashboard.stripe.com`) → confirm the actual account/legal
business, supported country, payout/tax details and test/live mode. Record
account ID. Product catalog → approved product and price/currency/cadence; record
generated product/price IDs separately per mode. Developers/Workbench → API keys
and Webhooks → event destination: register only the **merged A4 webhook URL and
required event set**, record endpoint ID and signing-secret reference. Configure
approved customer-portal return URLs and checkout success/cancel URLs to the
actual application routes. API secret and webhook signing secret are distinct.

**Baseline has no Stripe route, SDK, config variables, prices, entitlement or
ledger.** There are therefore no working Stripe environment variables/callbacks
to prescribe here yet. A4 must publish the exact contract and tests; A12 then
updates this section before activation. Do not issue a live charge, choose
pricing, invent IDs, or treat checkout success navigation as settled credit.

Verification after merge: signed test-mode webhook, invalid signature refusal,
duplicate/out-of-order delivery, concurrent reservation, insufficient balance,
settlement/refund, chargeback/subscription transitions if supported, and a ledger
reconciliation against the real test-mode event IDs. Record the owner-approved
procedure for moving to live keys/prices; test-mode proof is not live revenue.

## 6. Gateway, database, storage and keys

**Owner:** Service/database/storage administrator and security key custodian.

The current product Gateway uses SQLite, not `DATABASE_URL`. The root
Postgres/RabbitMQ/Redis variables and Docker compose belong to the retained
Django stack. Do not deploy that stack as the Native Magi API.

Set the service's private mode-0600 environment with actual values:

| Name | Required source / meaning |
|---|---|
| `MAGISTRATE_ENV` | `production` |
| `MAGISTRATE_DB_PATH` | Absolute persistent SQLite path outside every release checkout |
| `MAGISTRATE_SECRET_KEY` | Generated Fernet key from approved secret generation; store version in secret manager |
| `MAGISTRATE_BOOTSTRAP_SECRET` | Independently generated high-entropy operator credential; never a tester credential |
| `MAGISTRATE_BOOTSTRAP_USER_ID` | Approved operator principal; not a tenant supplied by clients |
| `MAGISTRATE_CORS_ORIGINS` | Exact HTTPS frontend origins, no wildcard or path |
| `MAGISTRATE_DIST_DIR` | Actual exported frontend directory |
| `MAGISTRATE_DEV_AUTO_SESSION` | Unset or false in production |
| `MAGISTRATE_FRIEND_BETA_ENABLED` | False unless named restricted cohort explicitly approved |
| `MAGISTRATE_SESSION_TTL_SECONDS`, `MAGISTRATE_SESSION_SCOPES` | Reviewed short bearer lifetime and least authority |
| `MAGISTRATE_CHAT_UPLOAD_DIR` | Current private chat-file directory outside checkout; same storage/backup owner |

Keep DB parent mode 0700 and DB mode 0600. Retain previous encryption keys
through the approved restore/rotation window; `MAGISTRATE_SECRET_KEY_VERSION`,
`MAGISTRATE_PREVIOUS_SECRET_KEY`, `MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION`,
`MAGISTRATE_KEY_ROTATION_ENABLED` belong to the reviewed rotation procedure in
`gateway/scripts/rotate_credentials.py`, never an automatic startup rewrite.

Current avatar storage remains checkout-local/public; A10/A11 must converge it.
No cloud bucket SDK/environment contract exists yet. If the merged storage plane
uses object storage, its owner must record console/project/bucket/region/KMS key
IDs, public-access block, tenant prefix policy, encryption, lifecycle, versioning,
CORS and service-role credentials using that implementation's exact variable
names. No anonymous bucket or long-lived client cloud key is acceptable.

Before promotion take the guarded online SQLite backup plus file-storage
snapshot and required secret versions. Record checksum, deployment revision,
retention and recovery target. Rehearse restore to an isolated target, compare
all ledgers and upload bytes, verify encryption keys and revoked sessions, run
authenticated smoke, and measure recovery duration. Do not restore a backup over
an active DB or delete historical conversation/Pi rows. Repository restore tests
exercise synthetic data only. See [schema composition](SAAS_ARCHITECTURE.md).

## 7. Workers and model providers

**Runtime owner:** isolated Firstmate service account operator; A5/A11 own policy.

Record actual tenant/runtime mapping, service UID, private runtime home, immutable
producer root and pinned commit from `runtime/firstmate-producer.lock.json`.
Configuration names are `FM_HOME`, `MAGISTRATE_FIRSTMATE_ROOT`,
`MAGISTRATE_FIRSTMATE_TOOL_PATH`, `MAGISTRATE_FIRSTMATE_RUNTIME_HOME`, and
`MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED`. Required producer mode validates the
reviewed installation/outbox, not an inferred process. Use the install/verify
contract in [firstmate-pinned-producer.md](firstmate-pinned-producer.md) only in an
approved operator environment. Runtime home is state; producer root is code.

Do not place harness keys, runner addresses or sockets in the client. The
optional `MAGISTRATE_EXECUTION_INVENTORY` describes verified harness/provider/
model/variant profiles, not invented availability. Credentials remain encrypted
server-side. Worker production isolation needs A5/A11's tenant boundary; a shared
HOME, Docker socket mount or `command` scope is not isolation. Runtime activation
is a separate authorized operation; reads and this release runner never start,
stop, restart or inspect Herdr lifecycle.

**Model owner:** OpenAI organization/project administrator in `platform.openai.com`
→ actual organization/project → API keys, limits and usage. Configure a restricted
project key in `OPENAI_API_KEY`; set approved `MAGISTRATE_MAGI_MODEL_PROVIDER`
(currently only `openai`), `MAGISTRATE_MAGI_MODEL`, `MAGISTRATE_MAGI_PROVIDER_URL`
(credential-free HTTPS Responses API base), `MAGISTRATE_MAGI_TIMEOUT_SECONDS`,
`MAGISTRATE_MAGI_MAX_OUTPUT_TOKENS` and optional
`MAGISTRATE_MAGI_REASONING_EFFORT`. Speech uses `VOICE_STT_PROVIDER`,
`VOICE_STT_MODEL` and `OPENAI_BASE_URL`; it is not necessarily the chat base URL.

Record organization/project and model IDs, spending limits, data-retention terms,
key reference and observed usage. Verify a real bounded non-streamed Responses
turn, refusal/timeout handling, STT and charged usage. A configured health flag
performs no live probe. Other provider/harness console settings must come from
A7's merged adapters; setting `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` alone does
not add a Native Magi provider. Complete the seven moat tests after replacement.

## 8. DNS, TLS, edge monitoring and deployment

**Owner:** Registrar/DNS-zone owner, hosting/network administrator and on-call.
Record actual zone/account and host/service identities. Configure A/AAAA or CNAME
records to the approved ingress, issue/renew a certificate covering the actual
app/API hostnames, force HTTPS, forward WSS `/api/v1/events` upgrades, and disable
shared caching of auth and owner data. Restrict internal worker/DB surfaces from
public access. Set proxy body/time limits aligned to bounded Gateway routes,
review rate limits and same-site cookie behavior. Verify from outside the host,
not only loopback. A 401 proves reachability, not authenticated readiness.

The existing demo workflow is manual-only and stays so. Repository Actions
settings → Secrets and variables → Actions currently names:
`MAGISTRATE_DEPLOY_HOST`, `MAGISTRATE_DEPLOY_USER`,
`MAGISTRATE_DEPLOY_SSH_KEY`, `MAGISTRATE_DEPLOY_KNOWN_HOSTS`; optional variable
`MAGISTRATE_DEPLOY_PORT`. No bootstrap/provider key goes into Actions.
Do not dispatch it merely to gather evidence. Authorized deployment uses
`scripts/deploy_magistrate.sh` safeguards and exact environment validation.

Trusted smoke: `scripts/smoke_magistrate.sh` on the authorized host. External
Friend Beta smoke: `scripts/smoke_friend_beta.sh` with
`MAGISTRATE_FRIEND_BETA_GATEWAY_URL`, a private
`MAGISTRATE_FRIEND_BETA_ACCESS_FILE`, and `MAGISTRATE_SMOKE_PYTHON`. Use a dedicated
smoke grant, never Spencer's enrolled grant; it exchanges and revokes that grant.
It makes no paid model request and is not device proof. On failure verify/revoke
the smoke grant through the operator CLI.

Monitoring provider/console and ingestion env contract are not selected in this
baseline. The operations owner must record the real project/DSN secret reference,
redaction/retention settings, alert destination/on-call rotation, uptime probes,
DB/storage capacity, provider/queue failures, webhook lag, backup freshness and
an actual test alert. Do not log authorization/cookie/query secrets, user prompts,
private decision answers or raw worker transcripts. Configure GitHub rulesets to
require the merged CI checks and protected release approval; branch protection
and console access are external evidence, not repository code.

## 9. Push, EAS, TestFlight and App Store

**Owner:** Expo organization administrator and Apple release/signing owner.
Committed identifiers (not verified console access): Expo owner `melkezics-team`,
project `aedf2c07-8f71-4e31-9e6e-7968f30479b1`, slug `magistrate`, iOS bundle and
Android package `io.magistrate.cockpit`.

1. `expo.dev` → actual organization → Magistrate project → Environments: configure
   each development/preview/production `EXPO_PUBLIC_GATEWAY_URL` and public auth
   client IDs above. Optional `EXPO_OWNER` / `EXPO_PUBLIC_EAS_PROJECT_ID` overrides
   must match committed IDs; never silently create a different project.
2. Apple Developer → Keys: approved APNs-enabled key (Team ID/Key ID and `.p8`);
   Expo project Credentials / EAS credentials: bind the correct bundle/team and
   push key and signing profiles. For an Android release, configure the actual
   Firebase project/service-account FCM V1 credential in EAS and platform config;
   Android device acceptance is additional, not covered by iPhone evidence.
3. `MAGISTRATE_EXPO_PUSH_URL` is an optional server send endpoint override, not an
   APNs credential. Normal delivery uses Expo's send API. Configure native
   credentials at EAS, not in `EXPO_PUBLIC_*`. Record permission/token registration
   and actual provider ticket **and receipt**, invalid-token retirement and cold/
   warm deep-link behavior. Web fallback only works in an eligible open tab.
4. `appstoreconnect.apple.com` → Apps → create/confirm the iOS app for the bundle,
   legal entity, name and SKU chosen by its owner. Copy its actual numeric Apple
   app ID into `frontend/eas.json` at `submit.production.ios.ascAppId`. It is absent
   today; `${...}` placeholders are not interpolated there. Record Team ID,
   App Store Connect app ID and (if used) API Key ID/Issuer ID/private-key secret
   reference through approved EAS submission credentials.
5. On the clean candidate, with the matching EAS environment loaded:

   ```sh
   cd frontend
   npm run beta:preflight -- --profile production
   npx eas-cli@23.0.0 build --platform ios --profile production
   npx eas-cli@23.0.0 submit --platform ios --profile production
   ```

   These commands are owner actions, **not executed by this foundation**. The
   pre-install hook checks config; it does not replace release packet admission.
   Gate promotion with `production_acceptance.py verify` and operator review.
   Preview/internal/ad hoc builds are not TestFlight. Capture EAS ID, Git SHA,
   app/build numbers, archive hash, entitlements and secret scan.
6. In App Store Connect complete processing/export questions, TestFlight groups
   and external beta review where required. Install the exact build on recorded
   physical iPhone/iOS versions and Wi-Fi/cellular networks; run Spencer and
   [DEAT-001](DEAT-001.md). Build success is not install/notification/audio proof.
7. For store submission, the legal/product owner supplies real public privacy,
   terms, support and deletion URLs, legal seller/contact, data collection and
   tracking answers, subprocessors, age rating, content rights, screenshots,
   review notes and least-privilege review account. The repository's encryption
   flag is not legal export advice. Confirm current Apple digital-goods/payment
   policy against the actual billing UX before enabling Stripe purchase links.
   No legal entity, price, URL, account ID or approval is inferred here.

## 10. Mandatory Spencer and moat acceptance

Export the exact checkpoint requirements with:
`python3 scripts/production_acceptance.py list`. Create the evidence template
for the **merged candidate SHA**; all external checkpoints initially say
BLOCKED_EXTERNAL. Run Spencer as a genuinely new user, no SSH/bootstrap/runner
configuration or preseeded profile. Execute in order: sign-in → name onboarding →
GitHub/project → billing/budget → real objective/evidence → Attention answer →
file/voice → offline/reopen/second device → push/deep link → account retirement.
Keep failed attempts as evidence; rerun after fixing, never overwrite them as a
pass. Billing tests must use owner-approved test mode, not an unauthorized charge.

Moat requires seven independent records: real provider replacement, harness
replacement, authorized context continuity, cost routing, client-independent
background work, the full Attention loop, and device independence. Injected
models and stubs establish only repository seams. Record fake-vs-live boundaries
explicitly. A human release authority reviews every artifact hash/attestation,
then updates [the matrix](PRODUCTION_STATUS.md). No final RC or production claim
while any required row is FAILED or BLOCKED_EXTERNAL.
