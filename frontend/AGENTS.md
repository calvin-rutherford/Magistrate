# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.

## Web tests

Run the suites via the `test:*` scripts in `package.json`; each spawns its own Expo web server and drives headless Chrome through puppeteer-core.

Sharp edges when driving the web build in headless Chrome:
- `chrome-devtools-axi` reports screenshots saved but writes no file and its eval bridge omits `pageId`; drive headless Chrome with `puppeteer-core` (launch config in `tests/chat-terminal.web.test.js`) for visual verification instead.
- The Metro dev server can serve a stale bundle after edits; if changes don't appear on reload, restart `expo start --web` with `--clear`.
- `Appearance.setColorScheme` is not implemented by react-native-web; theme-mode overrides must go through the subscribable store in `src/services/ChatPreferences.ts` (`useChatColorScheme`).
- Expo's development-only `#error-toast` has a zero-sized box but can win hit-testing near the viewport bottom, silently swallowing clicks on the composer or drawer footer. Disable its pointer events first (see `tests/chat-terminal.web.test.js`).
- React Native `PanResponder` gestures do not fire from synthetic mouse drags; dispatch real touch events via the CDP `Input.dispatchTouchEvent` command instead.
- Cross-origin gateway mocks need in-page `fetch` patching (`page.evaluateOnNewDocument`); network-level request interception fails CORS preflights.
- Each suite pins a fixed dev-server port, so parallel git worktrees collide and silently drive *another* checkout's app. Override with `MAGISTRATE_WEB_TEST_PORT=<free port>` when running outside the primary checkout.

## Backend model selection contract

`POST /api/v1/captain/prompt` requires `harness` and `model` together or neither; omitting both keeps the backend's current session selection (see `gateway/app/main.py`). Offer variants from `GET /api/v1/execution/capabilities` only. Its response's `response` field is the agent's reply text and must be appended to the chat transcript by the caller — the gateway does not push it, so a caller that only checks for `status`/`error` will silently drop every reply.

## Voice input

`src/input/VoiceInputAdapter.ts` (`useVoiceInputAdapter`) is the one seam for microphone capture: it wraps `expo-audio` recording, exposes live `amplitude` (0-1, ~100ms cadence) for waveform UI, and on web also drives the Web Speech API for interim transcript callbacks. Pair `capture.stop()` with `transcribeVoiceAudio()` from `src/api/client.ts` to get a final transcript from the gateway's `/voice/transcribe` endpoint. Test it in headless Chrome by launching Puppeteer with `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream` so `getUserMedia` resolves without real hardware.

## Local dev environment

If `npx tsc`/`expo start` fails with `Cannot find module 'expo-*'` even though it's listed in `package.json`, the worktree's `node_modules` is stale — run `npm install` (not `npm ci`, to avoid rewriting the lockfile) before debugging further.

`CI=1 expo start --web` disables Metro file-watching: a long-running dev server keeps serving the bundle from launch time, so restart it after code edits (the `test:*` suites are unaffected — they spawn fresh servers).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
