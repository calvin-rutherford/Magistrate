# Deskless Operator Alpha

**Status:** historical owner-foundation slice. A later baseline internal EAS preview exists, but no physical-device result is claimed; current evidence is tracked in [`friend-beta-release-readiness.md`](./friend-beta-release-readiness.md).

## Product boundary

- **Magi** is the human interface: this native/web Expo client, its chat, voice,
  attention, and PR targets.
- **Magistrate** is the governed execution substrate: the HTTPS/WSS FastAPI
  Gateway, private Herdr connection, Firstmate, authorization scopes, and
  execution policy.
- **Owner alpha** is one trusted operator using command/voice scopes against one
  operator-owned Gateway/runner. It is not a friend preview, tenant system, or
  multi-user SaaS product.
- **Restricted Friend Beta preparation** now adds independently revocable,
  per-principal access grants and profile onboarding after this owner-alpha
  slice. Its safe default is `read,account,notifications`; this remains one
  shared deployment, not tenant isolation. See
  [`friend-beta-release-readiness.md`](./friend-beta-release-readiness.md).
- **Future multi-user production** still needs an identity provider, recovery
  and device administration, per-user authorization of every runtime source,
  and isolated execution. Friends never receive runner, provider, owner
  bootstrap, or execution credentials.

Normal chat follows `iPhone -> HTTPS Gateway -> provider-native Magi chat`.
Authenticated WSS carries application events; private Herdr/Firstmate remains
only behind fleet and retained compatibility surfaces. The app contains the
public Gateway URL, a short-lived bearer, and—only for native Friend Beta
enrollment—a SecureStore renewal grant. It never contains an owner bootstrap
secret, provider credential, Unix socket, runner address, or harness credential.

## Implemented foundation

### EAS and native configuration

`frontend/app.config.ts`, `frontend/app.json`, and `frontend/eas.json` provide:

- stable `io.magistrate.cockpit` iOS and Android identifiers, app-version
  runtime versioning, and the `magistrate:` URL scheme;
- pinned Expo SDK 57 `expo-dev-client` and `expo-secure-store`; release commands
  invoke the explicit `eas-cli@23.0.0` version;
- development, preview, and production channels/environments;
- a development profile that creates an internal **physical-device** build (it
  is not simulator-only), plus internal preview and store production profiles;
- build-time `EXPO_PUBLIC_GATEWAY_URL` validation. Native release environments
  must provide an HTTPS URL ending in `/api/v1`; its derived socket endpoint is
  WSS. The URL is public configuration, not a secret.

The repository is linked to the recorded Expo owner/project. Configure the
public Gateway URL in each authenticated EAS environment before building; the
native/legacy selection is committed per profile. A link or successful service
build is not signing inspection, TestFlight installation, or physical-device
acceptance, and none is fabricated here.

Example local config (do not commit a real host-specific value):

```sh
cd frontend
EXPO_PUBLIC_GATEWAY_URL=https://gateway.example/api/v1 \
EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED=true \
EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED=false \
npm run beta:preflight -- --profile development
npx eas-cli@23.0.0 build --profile development --platform ios
```

The runner stays private behind the Gateway. The cohort reaches only the
HTTPS/WSS edge, which still requires deployment-specific access control,
redacted monitoring, and request throttling.

### Session storage and bootstrap

`GatewaySessionStorage` is the one credential-storage seam. Native uses
`expo-secure-store` with device-only Keychain accessibility and does not fall
back to AsyncStorage. The existing validated route gate, bearer Authorization
header, expiry timer, revocation, logout, and 401 invalidation remain in place.
The old AsyncStorage bearer is never migrated; it is removed on native restore.

This owner-alpha slice intentionally had no refresh-token or one-time
trusted-device pairing flow. The later restricted Friend Beta access grant is
a revocable renewal credential stored by native SecureStore; the owner
bootstrap flow remains unchanged and is never persisted. Browser beta access
stores only the short bearer. This is still not OIDC, account recovery, tenant
isolation, or physical Keychain evidence.

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
(cd frontend && npm run typecheck && npm test)
(cd frontend && npx expo config --type public --json)
(cd frontend && npx expo export -p web)
(cd gateway && PYTHONPATH=. uv run pytest -q)
bash scripts/test_deploy_magistrate.sh
```

Repository automation and read-only EAS metadata cannot supply archive/signing
inspection, a physical iPhone result, APNs/FCM receipts, TestFlight processing,
or cellular HTTPS/WSS evidence. Those are explicitly open gates, not assumed
from export, web tests, or a build ticket.
