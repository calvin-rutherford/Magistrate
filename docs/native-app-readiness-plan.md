# Magistrate native iPhone friend-beta readiness plan

**Audit point:** `8d3cb38` (`origin/main`, 2026-08-30)  
**Scope:** Expo/React Native client, active FastAPI gateway path, deployment and CI.  
**Evidence rule:** No native build, TestFlight install, Siri invocation, Action Button invocation, push delivery, Bluetooth route, or physical-device behavior was claimed or observed. This is a plan, not an implementation.

## Executive decision

The smallest credible beta is a **restricted, single-operator deployment**: the iPhone talks only to an HTTPS gateway on the deployment host; the gateway alone reaches the local Herdr socket and Firstmate. Keep the runner and GitHub/provider credentials off testers' devices. Use EAS development builds for the owner and a TestFlight build for invited testers. Make native Voice Mode foreground-only for the first beta. Treat server-driven push and exact notification targets as required beta slices; treat true continuous background listening, streaming speech, automatic barge-in, and multi-user/isolated execution as later work unless the physical acceptance gate makes them mandatory.

A shared bootstrap secret and `default_user` are not a safe multi-user product. For a friends cohort, either (a) restrict access to trusted observers with `read`/`notifications` scopes and no command/voice scope, or (b) explicitly accept a trusted shared operator account for a very small cohort and document that it is not tenant isolation. The recommended beta is (a); a product owner must decide whether friends need command/voice access before implementation.

## What exists now

### Expo/EAS/native surface

- `frontend/app.json` has the app name/slug/version, scheme `magistrate`, bundle ID `io.magistrate.cockpit`, microphone/speech/photo/camera usage strings, `expo-router`, `expo-notifications`, `expo-audio`, and splash plugins.
- The iOS config declares `UIBackgroundModes` `audio`, `fetch`, and `remote-notification`, while the audio plugin explicitly sets `enableBackgroundRecording: false`. There is no implementation evidence for any of those background modes; declaring them should be reviewed before submission rather than treated as proof of background audio or fetch.
- `frontend/eas.json` has development (internal, **iOS simulator only**), preview (internal), and production (store) profiles. It has no EAS project ID, environment values, submit metadata, or device/TestFlight evidence.
- The installed repository is Expo `57.0.4`/React Native `0.86.0`; `npx expo --version` returned `57.0.6`. `expo-audio`, `expo-notifications`, and `expo-router` are installed at the expected 57 versions. `expo-dev-client` is not installed. `npm ls ... eas-cli` found no EAS CLI; `npx eas --version` failed with “could not determine executable to run.”
- `npx expo config --json` succeeds and resolves the metadata above, but reports `owner: null`, no `extra.eas.projectId`, and no runtime version. There are no `frontend/ios` or `frontend/android` directories or checked-in native project files. The Linux host has no `xcodebuild`, `xcrun`, `simctl`, CocoaPods, or Fastlane.
- Therefore Expo Go is the only currently evidenced client runtime. No EAS development/preview/TestFlight artifact has been built or installed.

### Routes and deep links

- `/` and `/home` resolve to the chat shell (`frontend/app/(tabs)/index.tsx`, `frontend/app/home.tsx`); `/chat` is the canonical chat route, `/voice` is the voice route, `/attention` accepts `item`, `/agents` accepts `agentId`, and `/pr-detail` accepts `number`.
- `frontend/app/_layout.tsx` blocks protected route mounting until an issued bearer session validates, starts notification monitoring after authentication, and stops it on `/voice`.
- `frontend/src/services/SiriShortcutAdapter.ts` only constructs `magistrate:` URL strings. It is not a native App Intent/Siri implementation and has no native module or App Shortcut registration behind it.
- The generated URL targets `/chat?record=true`, but `frontend/app/(tabs)/chat.tsx` reads only `agentId`; it never consumes `record`. `/voice` does auto-start capture about 180 ms after mounting, so a correctly routed `/voice` entry is the current candidate for launch-to-listening, subject to permission/session gating. No code currently binds Siri or the Action Button to it.
- Existing gateway URLs already distinguish Firstmate attention (`/attention?item=...`), GitHub PR detail (`/pr-detail?number=...`), and agent chat (`/chat?agentId=...`), but notification handling only pushes URLs beginning with `/attention`. Exact native target routing is incomplete.

### Authentication and secure storage

- `gateway/app/auth.py` issues opaque random bearer sessions after the configured bootstrap secret (or explicit development auto-session), stores only a SHA-256 digest, applies scopes, expires sessions after a bounded TTL, and supports revocation. Current defaults are one hour and scopes `read,account,providers,notifications,voice,command`.
- The client validates a restored session before mounting protected routes, schedules expiry, invalidates on expiry/401, and revokes on logout (`frontend/src/api/client.ts`). This lifecycle is covered by `frontend/tests/auth-lifecycle.web.test.js`.
- The session token and metadata are persisted in `AsyncStorage` under `magistrate.gateway.session`. `expo-secure-store` is absent. AsyncStorage is not an iPhone Keychain-backed secret store. There is no refresh-token flow; expiry requires a new bootstrap exchange.
- HTTP uses `Authorization: Bearer`; the events WebSocket authenticates in its first application frame rather than a query string. This is a useful baseline. The active deployment remains single-user by `MAGISTRATE_BOOTSTRAP_USER_ID` and does not provide invites, OIDC, tenant isolation, or per-user runner authorization.
- Provider OAuth state is server-side, expiring, principal-bound, one-use, and redirect-allowlisted. GitHub OAuth is usable only when real gateway credentials and callback configuration are supplied; no native OAuth/device evidence exists.

### Notifications

- `frontend/src/services/NotificationManager.ts` installs a native notification handler and, on native, polls `/notifications/events` every 10 seconds while the authenticated app is mounted. It can schedule a **local** notification and uses the in-app fallback when permission is absent. Web notifications are explicitly page-open browser notifications, not service-worker push (`docs/notifications.md`).
- The client never calls `/notifications/register`; there is no Expo push-token registration or token refresh handling.
- The gateway has `/notifications/register`, a `push_tokens` table, and `send_push_notification()` posting to Expo Push Service. No production event worker calls `send_push_notification`; attention is only evaluated when `/notifications/events` is requested. The token table is keyed by `user_id`, so one account overwrites another device's token.
- Notification data uses `url`, and `copyFor()` collapses multiple events to `/attention`; the response listener only routes `/attention`. This is not evidence of background push or exact PR/agent delivery.
- Firstmate/Herdr attention records are real-schema-based and carry keyed attention IDs. GitHub records currently route to `/pr-detail`; agent rows route to `/chat?agentId=...`. No approval/rejection action route is present in the current attention UI.

### Voice and audio

- `frontend/src/input/VoiceInputAdapter.ts` is the single capture seam. It uses `expo-audio` high-quality recording with metering, stores in cache, requests microphone permission, and returns native `.m4a`/web `.webm`. Web Speech API supplies interim text only on web.
- The voice page uses a 1.2-second quiet timer, 30-second maximum turn, final file upload to `/voice/transcribe`, a synchronous voice move, Expo TTS, and a loop back to listening after TTS. The gateway uses OpenAI transcription with a 25 MB limit and returns final text.
- Native partial transcription is absent. Audio capture stops before transcription/TTS; speech during TTS is not monitored. Barge-in is a user tap that stops TTS and starts another turn, not automatic voice activity detection. No AirPods/Bluetooth route selection, route-change listener, interruption listener, echo cancellation, or media-services-reset recovery is implemented.
- The installed Expo Audio 57 types expose `interruptionMode`, `shouldPlayInBackground`, `allowsBackgroundRecording`, recorder input enumeration, and `setInput()`. The application does not use these APIs. This is an API capability inventory, not proof that the desired device behavior works.
- A background audio entitlement and a foreground recorder are not equivalent. With `enableBackgroundRecording: false` and no tested background lifecycle, the beta must state that voice stops or pauses when backgrounded/locked. Incoming calls, Siri, another audio app, and media-service resets are unverified.

### Endpoint and private-runner boundary

- `frontend/src/api/client.ts` uses `EXPO_PUBLIC_GATEWAY_URL`; browser builds default to same-origin, but a native build with no variable defaults to `http://localhost:8000/api/v1`. The production guard rejects non-HTTPS except localhost, which would still be a broken/unsafe native production default.
- `frontend/src/realtime/socket.ts` derives `ws`/`wss` from that URL. The gateway WebSocket endpoint is `/api/v1/events`; a TLS reverse proxy must preserve WebSocket upgrade and the first-frame authentication contract.
- `docs/deployment.md` documents a persistent deployment checkout, fail-closed production settings, absolute external SQLite state, and a guarded fast-forward deploy. `FM_HOME` and the default Herdr socket are local host concerns. The deployment workflow and deploy script still use host-local/plain HTTP health probes; those are not an iPhone production endpoint.
- There is no authenticated remote runner protocol, Tailscale ACL contract, Postgres/Redis deployment, or multi-tenant execution boundary in this repository. For a restricted beta, co-locate gateway and Herdr on the private runner and expose only the gateway through a TLS reverse proxy. Do not put a Tailscale address, Unix socket, runner credential, or provider secret in the app. A separate runner with mTLS/service identity and per-tenant routing is future production work.

## Smallest implementation slices

1. **Choose and document the beta boundary.** Use one operator-owned gateway/runner. Prefer observer sessions with `read,notifications`; if chat/voice is required, explicitly accept trusted shared execution and limit the cohort. Keep SQLite on an absolute persistent path for this beta. Do not call this multi-user.
2. **Create a real EAS project and device build path.** Install/pin EAS CLI in the release procedure, authenticate the Expo account, run the project link/init to obtain `owner`/`extra.eas.projectId`, add `expo-dev-client`, and change the development profile from simulator-only to a physical-device development build. Configure EAS environment `EXPO_PUBLIC_GATEWAY_URL=https://<gateway-host>/api/v1` for native builds; do not embed any secret. Build and install an EAS development build on one physical iPhone, then build an internal preview. Use TestFlight production distribution for friends rather than ad hoc UDID distribution unless the cohort is strictly device-registered.
3. **Move the session secret to Keychain.** Add `expo-secure-store` and a small storage abstraction used by `client.ts`; store the bearer and expiry/scopes there, keep only non-sensitive UI state in AsyncStorage, and clear both on logout/401/expiry. Decide whether the beta re-enters the bootstrap secret after expiry (smallest path) or adds a server refresh endpoint. Never migrate an unvalidated old AsyncStorage bearer into a trusted session without validation. Add native tests for cold launch, expired/revoked session, offline restore, logout, and concurrent validation.
4. **Register and deliver real push.** After authenticated native startup, request permission in an explicit user action, call `Notifications.getExpoPushTokenAsync({ projectId })`, register the token plus platform/device identity, and update/unregister it on token changes/logout. Replace the one-token-per-user table with device-token rows before any multi-device cohort. Add a gateway scheduler/worker (or a deliberately operated poller) that evaluates the real attention feed, deduplicates revisions, calls Expo Push Service, records receipts/errors, and invalidates bad tokens. Keep local polling/in-app fallback as a recovery path, not as claimed background delivery.
5. **Centralize target routing.** Define a versioned payload such as `{target_type, target_id, route}` with allowlisted routes: attention ID, agent ID, and PR number. Handle warm responses, cold-start last response, and app URL events in one root-level router. Route `/attention?item=...`, `/chat?agentId=...`, and `/pr-detail?number=...`; preserve a pending `autostart` intent while session validation is in progress. Add notification tests for duplicate, denied, terminated, warm, cold, and each target type.
6. **Implement the minimum native voice contract.** Point shortcuts/deep links at `/voice?autostart=true`, consume that parameter, and ensure auth/permission errors return to a visible state rather than silently recording. Keep the first beta foreground-only: start/stop/final transcription/TTS, visible recording state, explicit cancellation, and a tap-to-interrupt path. Configure and test the Expo Audio mode for play-and-record without claiming background recording; use the recorder input APIs only after confirming their native behavior. Add AppState cancellation/resume and interruption/media-reset handling. After this is stable, add a native VAD/streaming audio component for automatic barge-in, partial STT, duplex echo control, and AirPods route changes. Do not enable background audio merely to satisfy the plist; review/remediate unused background modes.
7. **Add the iOS launch surface.** The credible native implementation is an iOS App Intent/App Shortcut (likely a small Swift native module/target) that opens the main app at the allowlisted voice URL. Siri can invoke the shortcut; on supported iPhones, the user can assign that shortcut to the Action Button. The intent must not claim it can capture microphone audio while the app is suspended: it opens the app, then the app requests/uses permission and starts Voice Mode. Verify the needed Apple capability/entitlement in the signed archive. A custom URL entered in the Shortcuts app is a fallback experiment, not evidence of native Siri/App Intent support.
8. **Make the endpoint and runner production-safe.** Provision DNS/TLS, reverse-proxy HTTPS and WSS to the gateway, configure strict web CORS, and verify the gateway from cellular without Tailscale on the phone. Keep gateway-to-Herdr on the runner's local socket/private network. Use the existing production secret requirements (`MAGISTRATE_ENV`, external `MAGISTRATE_DB_PATH`, bootstrap secret, Fernet secret, CORS) and add the real STT/GitHub OAuth secrets through host secret management. Keep a future runner service identity/ACL package separate from this restricted beta.

## Credentials and capabilities required

- Apple Developer Program team access; registered bundle ID `io.magistrate.cockpit`; signing/provisioning managed by EAS; App Store Connect app record and TestFlight access for the invited cohort.
- Expo/EAS account and project ID; physical iPhone UDID only for internal development builds; `expo-dev-client` in the development build.
- APNs capability/credentials through the selected Expo Push Service/EAS setup, plus a tested Expo project ID. If direct APNs is selected instead, an APNs key/certificate and server-side key storage are required.
- Native App Intent/App Shortcut/Siri capability decision and signed-archive verification. Action Button testing requires supported iPhone hardware and user assignment of the shortcut; it is not a generic Expo runtime switch.
- Production DNS certificate/reverse proxy with WebSocket upgrade; gateway production secrets; OpenAI STT key; real GitHub OAuth client ID/secret and exact callback if GitHub is in beta; host-only Tailscale ACL/service identity if gateway and runner are separated.

## Restricted beta versus future production

| Area | Restricted friend beta | Future multi-user production |
|---|---|---|
| Identity | One operator deployment; unique short-lived session; trusted invite/observer scope | OIDC/invite accounts, refresh/revocation, device/session management, tenant authorization |
| Runner | Gateway co-located with one private Herdr/Firstmate runner | Per-tenant isolated runner or authenticated runner service with mTLS/ACLs |
| Data | Persistent external SQLite with backup and one operator profile | Managed Postgres, migrations, shared event/queue infrastructure, retention/audit policy |
| Push | Expo Push Service, device rows, actionable Attention/PR events, explicit fallback | Multi-device fanout, receipts, token lifecycle, per-user preferences and event workers |
| Voice | Foreground-only final STT/TTS, tap interrupt, honest background stop | Streaming STT/TTS, VAD/echo cancellation, automatic barge-in, route/interruption recovery |
| Siri/Action Button | One App Shortcut opening `/voice`; physical proof required | Versioned intents, parameters/targets, analytics, permission and auth recovery |
| Providers | Only real configured GitHub OAuth; other providers visibly unavailable | Per-user OAuth, refresh/revoke, capability scopes, provider health and secret rotation |

## Acceptance matrix (must be run on a physical iPhone)

Record device model, iOS version, build number, gateway revision, network, timestamps, result, logs, and defect ID. A green web test is not a substitute.

| Area | Cases | Expected beta result |
|---|---|---|
| Install/auth | EAS dev install; TestFlight install; fresh launch; wrong bootstrap; valid bootstrap; kill/reopen; reload; expiry; server revoke; logout | No Expo Go; protected UI stays closed until validation; secure token survives relaunch; revoked/expired session returns to sign-in without leaking data |
| Wi-Fi/cellular | Wi-Fi request; cellular-only request; Wi-Fi↔cellular handoff; DNS/TLS failure; offline launch; restore network | HTTPS works externally; clear degraded/offline state; retry succeeds; no localhost/private-runner address is required |
| Reconnect | Drop WSS during chat; gateway restart; app background/foreground; repeated navigation/unmount | At most one socket; bounded reconnect/poll fallback; no duplicate messages; foreground resumes synchronization |
| Push | First permission; denied permission; token registration; server-created Attention; PR event; app foreground/background/terminated; warm/cold tap; duplicate/revision; quiet hours; invalid token | System notification only when provider confirms accepted delivery request; tap lands on exact Attention/agent/PR target; fallback remains honest |
| Voice permission | First permission; deny; enable in Settings; cancel; short turn; 30-second limit; STT failure; no network | Visible state and recoverable error; no hidden capture; no request on too-short/failed recording |
| Voice loop | 3–5 minute multi-turn Firstmate conversation; full transcript; TTS; tap interruption; action confirmation; end session | No lost turns or duplicate sends; complete text visible; explicit high-impact confirmation; foreground-only background behavior documented |
| Audio route | Built-in mic/speaker; AirPods connected before start; connect/disconnect mid-session; phone call/Siri/other media; media service reset | Route/interruption outcome recorded; no claim of automatic AirPods/barge-in until verified; safe stop/recovery on interruption |
| Background | Lock screen/background while listening, thinking, speaking; notification while app terminated | Beta stops/pauses voice honestly; no background recording claim; push path is independent of app polling |
| Deep links | `/voice?autostart`; `/attention?item`; `/chat?agentId`; `/pr-detail?number`; cold launch while unauthenticated | Exact route/target preserved through auth; voice starts only after permission/session is ready; invalid targets show safe fallback |
| Security | Inspect archive/bundle; inspect logs; test missing/expired/revoked/insufficient scopes; attempt query-token auth; provider callback replay | No bootstrap/session/provider/runner secret in bundle or logs; query-only credentials rejected; callback state one-use and principal-bound |

## Current validation evidence and blockers

- `cd frontend && npx tsc --noEmit`: passed.
- `cd frontend && npm test`: passed, 63 hermetic tests across agent status, ambient, history, voice, auth, chat, shell, home, GitHub, and notifications.
- `cd gateway && PYTHONPATH=. uv run pytest -q`: passed, 120 tests with one Starlette/httpx deprecation warning.
- `cd frontend && npm run lint`: failed with 24 errors and 19 warnings, mostly React effect/ref rules plus one notification JSX entity error. CI does not currently run lint.
- Deployment contract is covered by `scripts/test_deploy_magistrate.sh`; the deployment workflow verifies plain host HTTP reachability, not an authenticated external HTTPS/WSS client path.
- Blocking evidence gaps are the absent EAS project/CLI/build, absent physical-device host tooling, absent SecureStore, absent native push registration/server delivery worker, absent exact native target router, absent App Intent/Action Button implementation, and absent physical audio/network/background acceptance. These are not resolved by the passing web/gateway suites.

**Recommended gate order:** decide beta identity/scope → produce EAS physical build → secure session storage → HTTPS/WSS external smoke → push registration/delivery/target routing → foreground voice/audio and deep-link tests → App Shortcut/Siri/Action Button proof → full matrix → only then invite 3–5 testers. Do not describe the app as a native friend beta before those gates have evidence.
