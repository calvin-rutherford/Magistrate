# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.

## Web tests

`npm test` runs the whole suite and is the command CI runs; individual suites are the `test:*` scripts in `package.json`.
`tests/helpers/web-server.js` owns the shared lifecycle: each suite gets an OS-assigned free port, a readiness probe that fails
loudly (with the server's own output) instead of silently binding to another checkout's dev server, and a teardown that waits for
the server to exit. Set `MAGISTRATE_WEB_TEST_PORT` only to pin one suite to a known port for debugging.
The drawer/fleet cases in `chat-terminal.web.test.js` used to be written off as flaky; they were fixed-port cross-talk with a concurrent
worktree running the same suite, and they pass reliably now. Treat a new intermittent failure as a real signal, not as known noise.

Sharp edges when driving the web build in headless Chrome:
- `chrome-devtools-axi` reports screenshots saved but writes no file and its eval bridge omits `pageId`; drive headless Chrome with `puppeteer-core` (launch config in `tests/chat-terminal.web.test.js`) for visual verification instead.
- The Metro dev server can serve a stale bundle after edits; if changes don't appear on reload, restart `expo start --web` with `--clear`.
- `Appearance.setColorScheme` is not implemented by react-native-web; theme-mode overrides must go through the subscribable store in `src/services/ChatPreferences.ts` (`useChatColorScheme`).
- Expo's development-only `#error-toast` has a zero-sized box but can win hit-testing near the viewport bottom, silently swallowing clicks on the composer or drawer footer. Disable its pointer events first (see `tests/chat-terminal.web.test.js`).
- React Native `PanResponder` gestures do not fire from synthetic mouse drags; dispatch real touch events via the CDP `Input.dispatchTouchEvent` command instead.
- A horizontal `PanResponder` swipe on web (e.g. `app/(tabs)/chat.tsx`'s drawer open/close swipes) can be hijacked by the browser's own touch-based back/forward navigation gesture before your `onMoveShouldSetPanResponder` ever fires — this reproduces in headless Chrome too (`Input.dispatchTouchEvent`, look for a `framenavigated` to `about:blank`). Set `overscroll-behavior-x: none` on `document.documentElement` (see the web-only effect in `app/_layout.tsx`) and `touchAction: 'pan-y'` on the swipeable container's style.
- Cross-origin gateway mocks need in-page `fetch` patching (`page.evaluateOnNewDocument`); network-level request interception fails CORS preflights.
- Expo Router keeps a collapsed (0x0) copy of a screen mounted next to the visible one, so `page.locator(sel).click()` can bind to the hidden copy and then time out after 30s waiting for a box that never appears. Click through `clickRendered` from `tests/helpers/web-server.js`, which filters by bounding box and still issues a real mouse click.
- The deployed gateway may run behind `main`, so it can 404 newly added endpoints. To verify chat rendering against *real* herdr data, run this checkout's gateway locally against the real socket (`uvicorn app.main:app --port 8099` in `gateway/`) and set `EXPO_PUBLIC_GATEWAY_URL` in the test environment — gateway URLs are configuration, never embedded private runner addresses.

## Chat history rendering

The captain thread renders the gateway's canonical conversation record: `fetchCanonicalConversation` plus `conversation_messages` events, merged by `src/services/CanonicalConversation.ts` (append by canonical id, update on revision, order by `sequence_index`). Read `../CHAT_ARCHITECTURE_FIX.md` before changing it, and do not reintroduce text matching, optimistic counting, or prompt-boundary inference there.

Worker panes (`?agentId=`) are still terminal-derived: `fetchAgentHistory` bounded by `CHAT_HISTORY_LINES`, cursor paging on upward scroll, and `ChatIdentity` revision matching. Those branches are marked `TRANSITIONAL` in `app/(tabs)/chat.tsx`. Working agents can expose transiently empty snapshots, so consumers must tolerate them. A message the gateway discovered leaves `sentAt` unset unless the record carries a real `created_at`; a locally sent message keeps its original timestamp and no refresh may rewrite it.

## Backend model selection contract

`POST /api/v1/captain/prompt` requires `harness` and `model` together or neither; omitting both keeps the backend's current session selection (see `gateway/app/main.py`). Offer variants from `GET /api/v1/execution/capabilities` only. It returns the canonical turn it recorded in `conversation.messages`, which is what the caller renders; the legacy `response` field is provider text the gateway has already recorded and the captain thread ignores it.

## Voice input

`src/input/VoiceInputAdapter.ts` (`useVoiceInputAdapter`) is the one seam for microphone capture: it wraps `expo-audio` recording, exposes live `amplitude` (0-1, ~100ms cadence) for waveform UI, and on web also drives the Web Speech API for interim transcript callbacks. Pair `capture.stop()` with `transcribeVoiceAudio()` from `src/api/client.ts` to get a final transcript from the gateway's `/voice/transcribe` endpoint. Test it in headless Chrome by launching Puppeteer with `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream` so `getUserMedia` resolves without real hardware.

Voice input mode selection is persisted as `magistrate.voice.input-mode` by `ChatPreferences.ts`; capability definitions and fallback resolution live in `src/services/VoiceInputModes.ts`. Keep speech mode selection separate from execution harness/model routing, and keep gateway STT credentials server-side.

## Web push notifications

`src/services/NotificationManager.ts` polls `/api/v1/notifications/events` and delivers via the real `Notification` Web API on `Platform.OS === 'web'`; web only works in an open/eligible browser tab. Native obtains a real Expo token with `expo-notifications`, registers it through the authenticated Gateway, and relies on Gateway remote delivery (never a local notification fabricated from a foreground poll). Permission denial, missing EAS credentials, offline state, or provider failure keeps the item in the Attention drawer and its unread logo indicator; no in-app notification popup is used. The browser prompt is explicit and gated by `NotificationPermissionPreferences.ts`; verify with `npm run test:notifications`, which `npm test` and therefore CI both run.

## Local dev environment

`npm ci` is the install for a fresh checkout and for CI. If `npx tsc`/`expo start` fails with `Cannot find module 'expo-*'` even though it's
listed in `package.json`, the worktree's `node_modules` is stale — reinstall before debugging further.

`expo-env.d.ts` and `.expo/types` are generated and git-ignored, and `expo/types` is what makes react-native-web-only style props (`touchAction`,
`backdropFilter`) type-check. A fresh clone has neither, so local `tsc` can be *more* permissive than a fresh checkout's. `npm run typegen`
regenerates both non-interactively without touching `tsconfig.json`; CI runs it before `npm run typecheck` so both views agree.

`eslint.config.js` is committed. `expo lint` writes it if it's missing, which is why it used to show up as untracked drift after a lint run.

`CI=1 expo start --web` disables Metro file-watching: a long-running dev server keeps serving the bundle from launch time, so restart it after code edits (the `test:*` suites are unaffected — they spawn fresh servers).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
