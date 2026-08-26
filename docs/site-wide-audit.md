# Magistrate site-wide audit

> Status: evidence inventory, baseline checks, and initial UX, accessibility, reliability, security/privacy, and operations finding sets were recorded on 2026-08-26. Performance, mobile/web consistency, and test-coverage review remain incomplete, so this document does not yet represent a completed audit.

## Audit method and claim standard

This audit evaluates the checked-out `fm/site-audit-gnhf-s9` baseline across UX, accessibility, reliability, performance, security/privacy, mobile/web consistency, test coverage, and operations. A **confirmed defect** must be demonstrated directly by repository code, a reproducible behavior, or check output. An **opportunity/risk** identifies a plausible concern that still needs validation. Findings will retain that distinction and avoid treating a PR description or an unrun test as proof.

Repository paths and symbols are the durable evidence links. Line references will be used only where they add clarity and are unlikely to become misleading during the active-PR merge sequence.

## Checked-out baseline

- Branch: `fm/site-audit-gnhf-s9`.
- Audit base and initial `HEAD`: `6f5c61c` (`Add quiet actionable attention notifications (#2)`), identical to `origin/main` when inspected.
- Initial worktree: clean (`git status --short` produced no output).
- Applicable project instructions: root `AGENTS.md` reserves project-intrinsic knowledge for that file and points Expo work to `frontend/AGENTS.md`; `frontend/AGENTS.md` requires the exact Expo 57 documentation before changing Expo code. This audit changes documentation only.
- Recent history inspected through `git log --oneline --decorate --max-count=12`, spanning the notification and terminal fixes plus the earlier UI, architecture, and voice work back through the initial MVP.

## Architecture and inspection inventory

The repository contains three runtime surfaces that require separate review:

1. `frontend/`: Expo Router / React Native 0.86 application targeting native and web. File-based screens currently include the tab routes `index`, `account`, `attention`, `chat`, `fleet`, `map`, and `prs`, plus top-level `gesture`, `status`, and `voice` screens. The authoritative route files are under `frontend/app/`; `frontend/app/_layout.tsx` installs the root error boundary and notification monitor, and `frontend/app/(tabs)/_layout.tsx` declares the hidden tab navigator.
2. `gateway/`: FastAPI service with account/auth, GitHub, Jira, Teams, unified attention, notifications, voice transcription, fleet/agent, captain terminal, and agent key-input endpoints in `gateway/app/main.py`; it also exposes the AR WebSocket router in `gateway/app/ar_glasses.py`. Its package and Python constraints are in `gateway/pyproject.toml` and lock state in `gateway/uv.lock`.
3. `backend/`: Django/Channels/Celery government and agent domain. HTTP routing is in `backend/og_broker/urls.py`, the Magistrate WebSocket route is in `backend/agents/routing.py`, and models/services/tasks live under `backend/agents/`.

Configuration and operational entry points inspected include `README.md`, `docs/architecture.md`, `docker-compose.yml`, root and component dependency manifests, `Dockerfile`, `start_magistrate.sh`, `magistrate.sh`, `setup.sh`, `setup_server.sh`, `push_to_vps.sh`, and `pull_migrations.sh`. Test inventory includes `frontend/tests/`, `gateway/tests/`, `backend/tests/`, and `tests/e2e_live_test.py`. Performance, mobile/web consistency, and test-coverage source review is pending; baseline check results are recorded below.

## Active work and overlap boundaries

GitHub state was inspected with the required `~/.npm-global/bin/gh-axi` wrapper. At inspection time, the repository had three open PRs, all authored by `calvin-rutherford`, none with reviews, and none with configured CI checks (`0 passed, 0 failed`). PR descriptions report local validation, but this audit treats those reports as context rather than independently verified results.

| Workstream | Ref inspected | Diff against `origin/main` | Audit boundary / merge-order consequence |
| --- | --- | --- | --- |
| Ambient backgrounds and speech-reactive Voice Mode | PR #3, `origin/fm/ambient-backgrounds-b5` at `a8f2c66` | 15 files, +190/-157. Changes `EnvironmentBackground`, weather/theme services and assets, `WeatherBadge`, `voice.tsx`, recorder metering, and ambient tests. | Do not independently prescribe implementation of time/weather scenes, metered voice ripple, reduced-motion behavior, or high-contrast treatment already in this PR. Audit current-baseline defects separately, mark overlapping recommendations deferred to PR #3, and revalidate accessibility, privacy/configuration, performance, and native/web behavior after it lands. |
| Captain terminal history and scrolling | PR #4, `origin/fm/terminal-history-h8` at `2c864ce` | 6 files, +164/-29. Changes chat virtualization/follow behavior, terminal API/client history sizing, and web/gateway tests. | Do not duplicate scrollback expansion, virtualization, overlap-poll prevention, viewport preservation, or jump-to-latest work. Revalidate terminal UX, accessibility, API load, and regressions after PR #4; findings outside those boundaries may remain independently actionable. |
| GitHub/Home PR data and navigation | PR #5, `origin/fm/github-home-prs-g3` at `735aabd` | 15 files, +538/-127. Adds live/cached/paginated `gh-axi` PR data, Home/attention integration, in-app PR detail, external-link validation, and tests. | Defer replacement of mock/stale PR data, Home PR detail/navigation, pagination/cache/error handling, and external-link hardening to PR #5. Revalidate auth boundaries, parser resilience, UI states, notification compatibility, and performance after merge. |
| Quiet notifications | Baseline `6f5c61c`; feature ref `origin/fm/quiet-notifications-n6` at `31c242c` | The feature is already merged as PR #2 into `origin/main`; the historical feature diff touches 13 files across frontend and gateway notification/attention flows. | Audit notification behavior as baseline code, not future work. Do not characterize this as an active open PR. Findings must account for the merged transition/ack/preferences implementation and its existing tests. |
| Voice Mode prototype | `fm/voice-mode-prototype-a2` at `6f5c61c` | No commits ahead of the baseline; the ref resolves exactly to current `origin/main`. | There is no separate active Voice Mode diff to defer beyond PR #3. Audit the baseline `frontend/app/voice.tsx` and voice services normally, while treating PR #3's overlapping audio-reactivity changes as pending. |

The three open PRs overlap shared hotspots: PRs #3 and #4 both modify `frontend/package.json`; PRs #4 and #5 both modify `frontend/src/api/client.ts` and `gateway/app/main.py`; PR #5 also modifies notification-adjacent `frontend/app/(tabs)/attention.tsx`, `gateway/app/attention_service.py`, and `gateway/app/github_service.py` already changed by merged notification work. Recommendations touching these files must name dependencies and require post-merge revalidation rather than assume the branches combine cleanly.

## Evidence commands recorded

The following read-only commands established this inventory:

```text
git status --short
git branch --show-current
git branch -a --no-color
git log --oneline --decorate --max-count=12
git rev-parse --short <ref>
git log -1 --format='%h %s' <ref>
git diff --stat origin/main...<feature-ref>
git diff --name-status origin/main...<feature-ref>
rg --files
rg -n "@(app|router)\\.|app\\.(get|post|put|delete)|path\\(|urlpatterns|Stack\\.Screen|Tabs\\.Screen" backend gateway frontend/app -g '*.py' -g '*.tsx'
find frontend/app -maxdepth 3 -type f -name '*.tsx' -print | sort
find gateway/tests backend/tests frontend/tests tests -maxdepth 2 -type f | sort
~/.npm-global/bin/gh-axi pr list --state open --limit 20 --fields body,createdAt,url
~/.npm-global/bin/gh-axi pr view <3|4|5> --full
~/.npm-global/bin/gh-axi pr diff <3|4|5> --full
~/.npm-global/bin/gh-axi pr checks <3|4|5>
```

Refs compared: `origin/main`, `origin/fm/ambient-backgrounds-b5`, `origin/fm/terminal-history-h8`, `origin/fm/github-home-prs-g3`, `origin/fm/quiet-notifications-n6`, and `fm/voice-mode-prototype-a2`.

## Findings

### Confirmed defects

#### UX-01 — Home reports a phantom active agent and substitutes fixed agent details (medium; defer overlapping Home work to PR #5)

- **Evidence:** `frontend/app/(tabs)/index.tsx`, `HomeScreen`, renders `ACTIVE AGENTS ({activeAgents.length || 1})`, while its adjacent empty branch renders `No active agents.` when `activeAgents.length === 0`. The same agent-card branch renders `Firstmate Autonomous Control Loop` for every agent and uses `Claude 3.7 Sonnet` whenever `a.harness` is absent rather than exposing an unknown state.
- **Impact:** The same screen can simultaneously state that one agent is active and that no agents are active, and can present invented task/model details as observed fleet state. That undermines the Situation Room's primary status-summary purpose.
- **Boundary:** The contradictory empty count and fixed agent metadata are confirmed on the checked-out baseline by source inspection. PR #5 changes this Home route and its data presentation, so implementation should not race that work: revalidate after PR #5 merges and retain only behavior it does not correct.

#### A11Y-01 — Shared icon-only navigation controls have no accessible names or state (medium)

- **Evidence:** `frontend/src/components/BottomControls.tsx`, `BottomControls`, creates the Chat, Voice Mode, and Gesture controls as `TouchableOpacity` elements whose only children are SVG paths. None supplies `accessible`, `accessibilityRole`, `accessibilityLabel`, `accessibilityHint`, or `accessibilityState`; the current route is communicated only by changing the SVG stroke color. The component's styles also set the two outer hit targets to `40x40`, below the commonly used 44-point mobile target, while the center control is `52x52`.
- **Impact:** Screen-reader users cannot reliably identify the three primary destinations or determine which one is active. The outer targets also present a touch-target risk that needs device-level measurement because React Native hit slop and platform behavior are not established by this source inspection.
- **Boundary:** The missing semantic properties are a confirmed source defect; inadequate effective target size is a validation risk, not a confirmed failure. PR #3 changes the Voice Mode surface but not this shared component in its recorded diff, so semantic remediation can remain independent; recheck the voice control after PR #3 merges.

#### A11Y-02 — Voice Mode continuously animates without a baseline reduced-motion path (medium; defer implementation to PR #3)

- **Evidence:** `frontend/app/voice.tsx`, `VoiceScreen`, starts a 30 ms `setInterval` on mount and continuously changes the tetrahedron projection's rotation until unmount. The route does not query a reduced-motion preference or offer a pause control.
- **Impact:** Users who request reduced motion still receive persistent motion on a core interaction screen.
- **Boundary:** This is confirmed for the checked-out baseline by the unconditional interval. PR #3 explicitly changes the Voice Mode visualization and includes reduced-motion treatment, so no parallel implementation should be scheduled; verify the preference on native and web after PR #3 merges.

#### REL-01 — Shared API calls accept HTTP errors as successful application data (medium; coordinate with PRs #4 and #5)

- **Evidence:** In `frontend/src/api/client.ts`, the notification functions and `updateUserProfile` explicitly reject non-2xx responses with `if (!res.ok)`, but `fetchHealth`, `fetchRuntime`, `fetchAgents`, `fetchFleet`, `fetchAttention`, `fetchUserProfile`, `uploadUserAvatar`, all provider operations, `fetchGitHubPRs`, `transcribeVoiceAudio`, `fetchCaptainOutput`, `sendCaptainPrompt`, `interruptAgent`, and `sendAgentKey` immediately return `res.json()` without checking status. `frontend/app/(tabs)/chat.tsx`, `handleSend`, therefore clears the entered prompt before awaiting `sendCaptainPrompt` and schedules the success-path refresh for any JSON response, including FastAPI's JSON error bodies. `HomeScreen.loadData` in `frontend/app/(tabs)/index.tsx` converts rejected agent and PR requests to empty arrays, so transport and parsing failures are rendered as legitimate empty states with no user-visible degraded/error state.
- **Impact:** Rejected commands can appear submitted, and unavailable or unauthorized reads can be presented as valid empty data. This loses user input and obscures outages or authentication failures even though the server responded with an error.
- **Required outcome:** Centralize status-aware response handling with bounded, user-safe error information; preserve retryable user input; and give data surfaces distinct loading, empty, stale/degraded, and error states. Add client and screen tests for representative 401, 403, 500, non-JSON, network-failure, and successful responses. PRs #4 and #5 both modify `frontend/src/api/client.ts`, while PR #5 modifies Home/GitHub error handling; avoid a parallel conflicting implementation and revalidate or rebase this package after those PRs merge.

#### SEC-01 — A shipped shared token does not provide an effective authorization boundary (high)

**Evidence.** `frontend/src/api/client.ts` embeds both the gateway address and `DEVICE_TOKEN = 'magistrate-device-token-12345'` in client code and sends that value to account, fleet, agent-control, terminal, notification, voice, and provider endpoints. `frontend/src/realtime/socket.ts` also embeds the same token in a WebSocket URL. `gateway/app/auth.py` accepts that exact value by default when `MAGISTRATE_TOKEN` is unset and accepts credentials in either `X-Magistrate-Token` or the `token` query parameter. A web bundle or installed client necessarily exposes a client-side constant, so anyone who can obtain the client can reproduce this credential. The gateway's broad CORS policy in `gateway/app/main.py` (`allow_origins`, methods, and headers all wildcarded) does not restore an identity or per-device authorization boundary.

**Impact.** This is a confirmed design-level authorization defect, not a claim that the audited host is publicly reachable. If an attacker can reach the configured gateway, the shipped credential can authorize sensitive reads and actions including captain prompts and agent key input. Deployment reachability and network controls still require validation.

**Required outcome.** Remove production reliance on a credential distributed with the client; establish server-verifiable user/device identity, scoped authorization for sensitive operations, credential rotation/revocation, and an explicit allowed-origin policy. Preserve a clearly gated local-development mode if needed. The eventual task package must include negative authorization tests for every sensitive endpoint family and must revalidate PRs #4 and #5 because both change `frontend/src/api/client.ts` and `gateway/app/main.py`.

#### SEC-02 — OAuth state and callback redirects are attacker-controlled (high)

**Evidence.** `gateway/app/main.py` builds OAuth `state` as the unsigned string `f"{user_id}::{redirect_uri}"` in `connect_oauth_provider`; the caller supplies both values. The callback has no `verify_token` dependency, splits the received state, trusts its first component as the database user and its second component as the redirect target, then stores exchanged credentials for that user and redirects to that target. There is no random nonce, server-side transaction record, expiry, signature, one-time consumption, or redirect allowlist. The source comment itself says a real flow would generate state to prevent CSRF, confirming the current flow is a simulation rather than a completed protection.

**Impact.** The missing binding permits forged or replayed callback state and makes the callback an open-redirect surface. Whether a provider's authorization-code binding blocks a particular account-linking attack depends on that provider and must not be assumed; the absent CSRF/replay control and redirect validation are directly demonstrated.

**Required outcome.** Generate cryptographically random, expiring, single-use OAuth transactions bound server-side to the initiating authenticated principal, provider, and an allowlisted app redirect; reject missing, malformed, mismatched, expired, or replayed state before token exchange or persistence. Add provider-independent tests for each rejection case and a successful callback. PR #5 changes shared gateway/auth-adjacent surfaces and must be rebased or revalidated after this work.

#### SEC-03 — The backend command WebSocket accepts unauthenticated fleet-dispatch commands (high)

**Evidence.** `backend/og_broker/asgi.py` routes WebSockets directly through `URLRouter` without `AuthMiddlewareStack` or another authentication middleware. `backend/agents/routing.py` exposes `ws/magistrate/`, and `MagistrateConsumer.connect` in `backend/agents/consumers.py` unconditionally joins the shared event group and calls `accept()`. Its `receive` method accepts an arbitrary JSON `command`, creates or retrieves the default captain, and calls `ExecutiveService.launch_fleet`. There is no origin, identity, permission, schema/length, or rate check in this path.

**Impact.** Any client able to reach the backend WebSocket can observe shared events and request fleet creation. Network exposure remains deployment-dependent, but the application-layer absence of authorization is confirmed. The broker is published on host port 8000 by `docker-compose.yml`, increasing the importance of an explicit boundary.

**Required outcome.** Authenticate before accepting the socket, authorize command dispatch separately from event observation, validate and bound command payloads, enforce an origin policy where browsers are supported, and close unauthorized connections with a documented code. Add Channels communicator tests covering anonymous rejection, insufficient privilege, authorized dispatch, malformed/oversized payloads, and group isolation.

#### SEC-04 — OAuth credential encryption has a public default key and silently accepts plaintext (high)

**Evidence.** `gateway/app/db.py` defaults `MAGISTRATE_SECRET_KEY` to the repository-visible string `magistrate_super_secret_fernet_key_32bytes_len=` and deterministically derives the Fernet key from it. `decrypt_token` catches every decryption exception and returns the stored database value unchanged. Consequently, a deployment missing the environment variable uses a known encryption key, while corrupt or legacy plaintext is indistinguishable to callers from successfully decrypted secret material.

**Impact.** A copied database from a default-configured deployment does not have meaningful at-rest protection, and fail-open decryption can propagate unencrypted or corrupt credential content. This finding does not claim disk encryption is the only required control or that any production database was exposed.

**Required outcome.** Fail startup outside an explicit development mode when a suitably generated key is absent; version encrypted values; fail closed on authentication/decryption errors; define rotation/migration and secret-storage procedures; and test missing/invalid keys, ciphertext tampering, legacy migration, and rotation without logging secret values.

### Validation risks and opportunities

#### UX-02 — Voice Mode begins microphone capture on route entry (medium risk; validate consent expectations and PR #3)

- **Evidence:** `frontend/app/voice.tsx`, `VoiceScreen`, calls `startRecordingSession()` from its mount effect; that function immediately sets `LISTENING` and awaits `voiceInputAdapter.startRecording(...)`. The first explicit press of the tetrahedron stops/transmits an already-running session rather than initiating it.
- **Risk:** Automatic capture can surprise users who interpret entering the route as navigation rather than consent to record. Whether the operating-system permission prompt, prior product disclosure, and intended hands-free workflow make this acceptable requires UX/privacy validation and runtime testing.
- **Boundary:** Treat this as a risk, not proof of unlawful or undisclosed recording. PR #3 changes `voice.tsx` and recorder behavior; resolve the intended consent model there or perform post-merge revalidation rather than creating a competing Voice Mode package.

#### OPS-01 — Production-safety configuration is permissive by default (medium risk; validate deployment)

`backend/og_broker/settings.py` defaults to a committed Django secret, enables `DEBUG` unless explicitly disabled, and sets `ALLOWED_HOSTS = ['*']`. `docker-compose.yml` uses literal `password` credentials for Postgres and RabbitMQ and publishes the broker on `0.0.0.0:8000`. These defaults are directly confirmed, but actual production overrides, firewalling, TLS termination, and deployment topology were not available in the repository evidence. Validate the deployed environment before describing this as an externally exploitable defect. The target state is fail-closed production configuration, secret injection/rotation, constrained hosts, TLS at the documented boundary, and an automated deployment check that rejects development defaults.

#### SEC-05 — Avatar and voice uploads lack explicit application-level resource bounds (medium risk; validate infrastructure)

In `gateway/app/main.py`, `upload_account_avatar` and `transcribe_voice_input` call `await file.read()` with no declared content-length, streaming, media-type, or file-size enforcement; avatar content is written using a client-derived filename and served from `/uploads`. This confirms missing application-level bounds, but reverse-proxy limits and the practical memory/disk impact were not inspected. Validate infrastructure caps and malformed-file behavior, then add bounded streaming, filename generation independent of user input, media validation/decoding, safe storage permissions, and rejection tests if those controls are not already guaranteed upstream.

#### REL-02 — Realtime reconnect timers have no explicit ownership or cancellation (medium risk; validate runtime use)

`frontend/src/realtime/socket.ts`, `RealtimeClient.connect`, creates a new socket whenever it is called and schedules another `connect()` with a fixed three-second delay on every close. The class has no disconnect method, timer handle, connection-in-progress guard, exponential backoff, or duplicate-connect protection. This is a source-level lifecycle risk rather than a confirmed user-facing defect because no current import or invocation of the exported `realtimeClient` was found outside that module. Before activating this client, define a single owner, cancellable reconnect/backoff behavior, offline/app-state handling, and tests proving repeated `connect()` calls and unmount/disconnect cannot leave duplicate sockets or reconnect timers.

The future `docs/site-wide-task-list.md` must link each high-priority finding above to independently testable acceptance criteria. No implementation is recommended inside the active PR boundaries without the stated merge-order revalidation.

## Baseline verification

Checks were run independently on the checked-out baseline; active-PR validation claims were not counted. No long-running process remained after the commands completed.

| Surface | Exact command | Result | Interpretation / blocker |
| --- | --- | --- | --- |
| Frontend types | `cd frontend && npx tsc --noEmit` | Passed (exit 0). | The baseline type-checks with the installed dependencies. |
| Frontend terminal web test | `cd frontend && npm run test:chat` | Passed: 2 tests, 0 failures (exit 0). | Covers a bounded terminal viewport/wheel interaction and phone-sized composer input; it is not a site-wide browser suite. |
| Frontend lint | `cd frontend && npm run lint` | Failed: 7 errors and 11 warnings (exit 1). | No ESLint config was committed, so Expo first generated `frontend/eslint.config.js`; the generated file was removed immediately to preserve documentation-only scope. Reported errors include `react-hooks/set-state-in-effect` in the Account, Attention, Home, and PR routes and web color-scheme hook; `react-hooks/immutability` in `app/voice.tsx`; and `react-hooks/refs` in `EnvironmentBackground`. These are confirmed check failures, but each underlying user impact still requires source/behavior review before it becomes a prioritized defect. |
| Gateway tests, initial invocation | `cd gateway && uv run pytest -q` | Blocked during collection: 5 modules failed with `ModuleNotFoundError: No module named 'app'` (exit 2). | The project does not configure its local `app` import path for a plain pytest invocation. The corrected invocation below establishes the actual test baseline; this initial failure is still an operational/test-runner setup risk. |
| Gateway tests, corrected import path | `cd gateway && PYTHONPATH=. uv run pytest -q` | Passed: 23 tests, 1 warning (exit 0). | The warning is a Starlette deprecation from `fastapi.testclient`: use of `httpx` through that module is deprecated in favor of `httpx2`. |
| Backend tests | `cd backend && python3 -m pytest -q` | Blocked immediately: `/usr/bin/python3: No module named pytest` (exit 1). | No ready backend test environment was present. Installing dependencies would exceed this documentation-only iteration and could mutate environment/lock state; backend test status is therefore unknown, not passing. |

The frontend lint bootstrap created the only non-documentation artifact observed during checking; after removing it, `git status --short` showed only this audit document as changed. Full Expo web/native builds and the live end-to-end test were not run in this bounded baseline-check iteration and must not be inferred from the passing typecheck or terminal test.
