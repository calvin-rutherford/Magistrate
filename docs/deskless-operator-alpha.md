# Deskless Operator Alpha

**Status:** repository foundation only; no physical-device or EAS service result is claimed.

## Product boundary

- **Magi** is the human interface: this native/web Expo client, its chat, voice,
  attention, and PR targets.
- **Magistrate** is the governed execution substrate: the HTTPS/WSS FastAPI
  Gateway, private Herdr connection, Firstmate, authorization scopes, and
  execution policy.
- **Owner alpha** is one trusted operator using command/voice scopes against one
  operator-owned Gateway/runner. It is not a friend preview, tenant system, or
  multi-user SaaS product.
- **Future friend/multi-user product** needs real account/invite identity,
  per-user authorization, isolated execution, and device/session management.
  Friends must be issued restricted `read,notifications` sessions and must
  never receive runner, provider, bootstrap, or execution credentials. This
  slice does not add friend issuance.

The physical path is `iPhone -> HTTPS/WSS Gateway -> private Herdr ->
Firstmate/harnesses`. The app contains only the public Gateway URL and an
opaque, short-lived server session after bootstrap; it never contains a
bootstrap secret, provider credential, Unix socket, runner address, or harness
credential.

## Implemented foundation

### EAS and native configuration

`frontend/app.config.ts`, `frontend/app.json`, and `frontend/eas.json` provide:

- stable `io.magistrate.cockpit` iOS and Android identifiers, app-version
  runtime versioning, and the `magistrate:` URL scheme;
- pinned Expo SDK 57 `expo-dev-client`, `expo-secure-store`, and local EAS CLI;
- development, preview, and production channels/environments;
- a development profile that creates an internal **physical-device** build (it
  is not simulator-only), plus internal preview and store production profiles;
- build-time `EXPO_PUBLIC_GATEWAY_URL` validation. Native release environments
  must provide an HTTPS URL ending in `/api/v1`; its derived socket endpoint is
  WSS. The URL is public configuration, not a secret.

The repository cannot create or link an Expo account project without Expo/Apple
credentials. Set `EXPO_OWNER`, `EXPO_PUBLIC_EAS_PROJECT_ID`, and the three EAS
environment values in the authenticated EAS project before building. No project
ID, signing result, TestFlight install, or physical-device result is fabricated
here.

Example local config (do not commit a real host-specific value):

```sh
cd frontend
EXPO_PUBLIC_GATEWAY_URL=https://gateway.example/api/v1 \
EXPO_PUBLIC_EAS_PROJECT_ID=<linked-eas-project-id> \
npx expo config --type public
npx eas build --profile development --platform ios
```

The gateway/runner stays private behind the deployment's TLS reverse proxy;
public internet-edge hardening remains follow-up work.

### Session storage and bootstrap

`GatewaySessionStorage` is the one credential-storage seam. Native uses
`expo-secure-store` with device-only Keychain accessibility and does not fall
back to AsyncStorage. The existing validated route gate, bearer Authorization
header, expiry timer, revocation, logout, and 401 invalidation remain in place.
The old AsyncStorage bearer is never migrated; it is removed on native restore.

There is intentionally no refresh-token or one-time trusted-device pairing
flow in this slice. Bootstrap is therefore a temporary operator-supplied
credential entry used to obtain a short-lived server session again after
expiry/revocation. The UI and storage seam leave room for a future pairing and
renewal exchange; repeated bootstrap entry is not represented as solved.
The browser retains its existing compatibility storage because browsers have
no Keychain; this is not evidence of native secure storage.

### Versioned pending intents

`PendingIntentRouter` is the single allowlisted parser/queue for version-1
intents:

- `/voice?autostart=true`;
- `/attention?item=<id>`;
- `/chat?agentId=<id>`;
- `/pr-detail?number=<positive integer>`.

Root layout captures initial URLs and URL events before authentication. Push
responses enqueue the same intent. An authenticated root consumes it exactly
once; malformed, external, unsupported, and duplicate targets are ignored.
This preserves the target across authenticated cold start, background,
terminated, duplicate, malformed, and unauthenticated launches. Voice starts
only after the authenticated route is mounted and microphone setup is ready.
The Siri adapter now points at the voice intent, but it remains a URL adapter:
there is no native App Intent, Siri registration, or Action Button implementation
and none is claimed.

### Push prerequisite seam

Native push registration is implemented as an explicit account action using
`expo-notifications`, a real EAS project ID, and authenticated Gateway token
registration. The Gateway's remote delivery payload now includes a versioned
app-owned target alongside the legacy URL. Permission denial, missing project
metadata, provider failure, offline Gateway, simulator, and Expo Go remain
unavailable/in-app fallback states.

Foreground polling and the in-app stack are recovery behavior, not server-driven
push. Real APNs/FCM delivery, receipts/worker operation, multi-device token
rows, and physical-device delivery evidence remain the next push seam. The
current server token record is still single-operator beta infrastructure and
must not be presented as multi-user device management.

### Voice and background honesty

Voice remains foreground-only final STT/TTS with explicit visible capture and
tap interruption. Unused `audio` and `fetch` background modes were removed;
`remote-notification` is retained for the push capability. There is no ambient
background listening, native Siri capture, Action Button handler, VAD, or
physical audio-route proof.

## DEAT-001 gate

The acceptance artifact is [`DEAT-001.md`](./DEAT-001.md). This slice adds the
named test plan and evidence fields but does not execute or pre-fill physical
results. A green TypeScript, web, Gateway, config, or export check cannot close
DEAT-001.

## Verification status

Run the direct checks from the repository root:

```sh
cd frontend && npx tsc --noEmit && npm test
cd frontend && npx expo config --type public --json
cd frontend && npx expo export -p web
cd ../gateway && PYTHONPATH=. uv run pytest -q
cd .. && bash scripts/test_deploy_magistrate.sh
```

The Linux worker cannot supply Apple signing, EAS account access, a physical
iPhone, APNs/FCM delivery, TestFlight, or cellular HTTPS/WSS evidence. Those
are explicitly open follow-up gates, not assumed from export or web tests.
