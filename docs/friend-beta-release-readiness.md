# Friend Beta release readiness

**Authority:** ordered release gate for the first invited Friend Beta cohort.
**Current verdict:** **repository prerequisites implemented; a baseline internal EAS preview finished; current-candidate distribution and physical-device gates NOT RUN.**

This is the shortest supported path after Native Chat Phase 1. It does not add
streaming, tools, autonomous execution, or a second chat transport.

## Fixed beta boundary

- Each friend/device receives a distinct `mgb_…` access grant assigned to a
  unique Gateway principal. The Gateway stores only its SHA-256 digest. It is a
  bearer renewal credential, not hardware attestation or cryptographic device
  binding; sharing it lets the later redeemer replace the earlier bearer.
- The safe grant default is `read,account,notifications`. It supports account
  onboarding and an observer preview against the one operator-owned deployment.
- `command` or `voice` reaches that shared operator runtime. The provisioning
  CLI refuses either unless the operator supplies
  `--allow-shared-runtime-access`. That flag records an operational
  acknowledgement; it does **not** create tenant isolation.
- The producer-only `response` scope can never be issued by the Friend Beta CLI.
- Native keeps the grant in SecureStore as a renewal credential and exchanges
  it for short-lived bearer sessions. Web stores only the short bearer. A new
  exchange revokes the previous bearer for that grant; successful online app
  logout or operator revocation retires the grant, every derived session, and
  that principal's push registration.
  Offline logout clears the device copy but cannot prove server revocation, so
  the operator must revoke that grant id before calling it retired.
- Provider credentials, owner bootstrap credentials, runner addresses, socket
  paths, and harness credentials remain server-side.

**Open product/security gate:** choose observer-only friends (recommended) or
explicitly accept shared-runtime `command`/`voice` for the named cohort. The
current generic `command` scope authorizes more than provider-native chat, so it
must not be described as isolated friend chat. A dedicated least-privilege chat
scope or isolated runtime is later work. Do not invite command-capable friends
until the merge authority records that choice.

## Ordered release procedure

### 1. Freeze cohort and authority

- [ ] Record every tester, device, intended scope set, grant expiry, and the
  communicated retained-data/deletion limitation.
- [ ] Record the observer-only/shared-runtime decision above.
- [ ] Set a cohort limit and one person responsible for revocation/support;
  configure redacted edge monitoring and request throttling.

### 2. Provision the Gateway

Configure the mode-`0600` production environment without printing it:

```dotenv
MAGISTRATE_ENV=production
MAGISTRATE_FRIEND_BETA_ENABLED=true
MAGISTRATE_NATIVE_CHAT_ENABLED=true
MAGISTRATE_LEGACY_CHAT_ENABLED=false
```

Retain all production requirements in [`deployment.md`](deployment.md), notably
an external persistent SQLite path, generated Fernet/bootstrap secrets, strict
HTTPS CORS, and a server-only `OPENAI_API_KEY`. The deploy guard refuses Friend
Beta with legacy captain chat.

Issue one code per person/device from a trusted shell after loading that exact
Gateway environment. The output path must be absolute, new, outside the release
checkout, and kept mode `0600`:

```sh
cd gateway
PYTHONPATH=. uv run python -m scripts.friend_beta_access issue \
  --user-id friend-alice-phone \
  --ttl-hours 168 \
  --output /secure/operator-only/friend-alice-phone.json
```

The default is observer access. Only after the explicit risk decision may an
operator issue shared-runtime access:

```sh
PYTHONPATH=. uv run python -m scripts.friend_beta_access issue \
  --user-id friend-alice-phone \
  --scopes read,account,notifications,command \
  --allow-shared-runtime-access \
  --ttl-hours 168 \
  --output /secure/operator-only/friend-alice-phone.json
```

Inspect/revoke without revealing a code or hash:

```sh
PYTHONPATH=. uv run python -m scripts.friend_beta_access list --user-id friend-alice-phone
PYTHONPATH=. uv run python -m scripts.friend_beta_access revoke --grant-id fbg_...
```

Transfer the code out of band, delete the transfer file after enrollment, and
keep the grant id in the operator's revocation record. One principal cannot
hold two active grants: revoke the recorded grant before reissuing that
principal, or assign a new principal for a replacement device.

### 3. Validate native build configuration

The EAS project and owner are linked in `frontend/app.json`, and each build
profile commits the provider-native-only public flags. A public Gateway URL
still must be configured in the matching EAS environment. No secret may use an
`EXPO_PUBLIC_` name.

```sh
cd frontend
EXPO_PUBLIC_GATEWAY_URL=https://gateway.example/api/v1 \
EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED=true \
EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED=false \
npm run beta:preflight -- --profile preview

EXPO_PUBLIC_GATEWAY_URL=https://gateway.example/api/v1 \
npx expo config --type prebuild --json >/tmp/magistrate-prebuild-config.json
```

The preflight checks the linked owner/project, bundle id, iPhone-only beta
scope, physical-device EAS profile, Keychain plugin, native-only chat selection,
public HTTPS endpoint,
export-compliance setting, and the absence of secret-shaped public variables.
It prints no environment values. EAS invokes the same fail-closed check through
`eas-build-pre-install`; the local command is the earlier operator check.

### 4. Deploy and exercise the external edge

Run the guarded deployment, then use a **dedicated smoke grant** from a trusted
host. This redeems that grant and therefore replaces any prior bearer for it;
do not reuse a tester's already-enrolled grant.

```sh
MAGISTRATE_FRIEND_BETA_GATEWAY_URL=https://gateway.example/api/v1 \
MAGISTRATE_FRIEND_BETA_ACCESS_FILE=/secure/operator-only/friend-smoke.json \
MAGISTRATE_SMOKE_PYTHON=/path/to/gateway/.venv/bin/python \
bash scripts/smoke_friend_beta.sh
```

The script proves HTTPS session exchange, validation, health, an owner-scoped
native transcript read, WSS first-frame authentication, and final smoke-grant
revocation. It sends no chat prompt and makes no paid provider call. A failed
run can stop before revocation, so list/revoke the grant manually after any
failure. This is external endpoint evidence, not physical-iPhone evidence.

### 5. Build and distribute preview

From a clean release commit, with authenticated Expo/Apple access and profile
environment values already set:

```sh
cd frontend
npx eas-cli@23.0.0 build --platform ios --profile development
npx eas-cli@23.0.0 build --platform ios --profile preview
```

Install the development build on the owner device first. Internal iOS preview
uses registered-device/ad hoc distribution; it is not TestFlight and should not
be used as the final friend channel.

### 6. Run physical acceptance

Execute every row in [`DEAT-001.md`](DEAT-001.md) on the exact candidate build.
At minimum record:

- fresh invite, name onboarding, kill/reopen renewal, access expiry, server
  revoke, and logout;
- Wi-Fi, cellular, network handoff, HTTPS, and authenticated WSS;
- owner native Magi persistence/reconnect without duplicate sends; observer
  submission refusal/no provider call, or friend submission only when the
  accepted scope decision authorizes it;
- notification permission/token/provider ticket and receipt versus in-app
  fallback;
- foreground voice permission, final STT/TTS, interruption, lock/background
  stop, and recovery on the owner device (and only an explicitly voice-scoped
  friend device);
- archive and log scans proving no access/bootstrap/provider/runner secret.

A simulator, Expo Go, web test, source inspection, or EAS ticket cannot mark a
physical row passed.

### 7. Create the TestFlight candidate

- [ ] Create/confirm the App Store Connect app for
  `io.magistrate.cockpit` and obtain its public numeric Apple app id.
- [ ] Put that real `ascAppId` in `eas.json`; do not use a `${...}` placeholder
  because EAS submit configuration does not interpolate it.
- [ ] Re-run `npm run beta:preflight -- --profile production`.
- [ ] Run `npx eas-cli@23.0.0 build --platform ios --profile production`.
- [ ] Inspect signing, entitlements, privacy/export answers, and the archived
  bundle; then run `npx eas-cli@23.0.0 submit --platform ios --profile production`.
- [ ] Install through TestFlight, repeat the release subset of DEAT-001, and
  only then add the named cohort.

### 8. Declare an RC only with evidence

An RC record must freeze the Git revision, EAS build id/build number, Gateway
revision, database backup id/hash, public endpoint, TestFlight build, test
device/iOS/network, test timestamps, grant policy, automated checks, DEAT-001
results, defects/waivers, and rollback owner. “Build submitted” is not an RC
acceptance result.

## Repository evidence available now

- Friend grant digesting, principal/scopes, renewal replacement, expiry,
  logout/revocation, elevated-scope refusal, and mode-`0600` CLI output have
  focused Gateway coverage.
- The app has a pre-auth Friend Beta code exchange and a required first-name
  onboarding gate before protected routes. Native renewal stays in SecureStore;
  stale account personalization is cleared on auth/principal changes.
- Preview/development profiles explicitly target physical devices. The duplicate
  iOS background declaration and invalid interpolated App Store id placeholder
  are removed. Production submit intentionally remains unlinked until the real
  App Store Connect record exists.
- Repository preflight and external smoke contracts are in CI.
- Read-only EAS inspection on 2026-09-13 observed a shape-valid public HTTPS
  Gateway URL in the preview environment; development and production had no
  Gateway URL, so those profiles remain build-blocked. The inspection also
  observed project `aedf2c07-8f71-4e31-9e6e-7968f30479b1` as
  `@melkezics-team/magistrate` and a finished iOS internal-preview build
  `a2278aad-4877-4e7e-a27a-951389ed903b` for baseline commit `564478a` on
  2026-09-13. That is evidence that the service produced a baseline artifact,
  not that this change is built, the archive is approved, a device installed
  it, or TestFlight works.

## Exact open gates

1. Merge-authority decision: observer-only versus named shared-runtime scopes.
2. Production Gateway configuration/deployment, DNS/TLS/CORS, persistent state,
   backup, redacted edge monitoring/rate limits, and real server-side model
   credential.
3. Verified EAS profile environment values plus a fresh signed build of the
   exact release commit; existing baseline preview artifacts do not cover it.
4. Apple Developer/App Store Connect record and real `ascAppId`.
5. APNs/Expo credentials plus provider ticket **and receipt** evidence.
6. Signed development/preview/production builds and TestFlight processing.
7. Physical iPhone Wi-Fi/cellular/WSS/auth/chat/notification/voice/archive
   evidence in DEAT-001, with defects closed or explicitly waived.

Until all seven gates close, report **repository-ready / release not accepted**.
Streaming remains out of scope and is not a release blocker.
