# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.

## Web tests

`npm test` runs the whole suite and is the command CI runs; individual suites are the `test:*` scripts in `package.json`.
`tests/helpers/web-server.js` owns the shared lifecycle: each suite gets an OS-assigned free port, a readiness probe that fails
loudly (with the server's own output) instead of silently binding to another checkout's dev server, and a teardown that waits for
the server to exit. Set `MAGISTRATE_WEB_TEST_PORT` only to pin one suite to a known port for debugging.
The Native Magi browser suite lives in `native-chat.web.test.js`; treat intermittent failures as real signal, not known noise.

Sharp edges when driving the web build in headless Chrome:
- Drive headless Chrome with `puppeteer-core` using the shared browser helper in `tests/helpers/web-server.js`.
- The Metro dev server can serve a stale bundle after edits; if changes don't appear on reload, restart `expo start --web` with `--clear`.
- `Appearance.setColorScheme` is not implemented by react-native-web; theme-mode overrides must go through the subscribable store in `src/services/ChatPreferences.ts` (`useChatColorScheme`).
- Expo's development-only `#error-toast` has a zero-sized box but can win hit-testing near the viewport bottom, silently swallowing clicks on the composer or drawer footer. Disable its pointer events before browser interactions.
- React Native `PanResponder` gestures do not fire from synthetic mouse drags; dispatch real touch events via the CDP `Input.dispatchTouchEvent` command instead.
- Never attach a `PanResponder` to the whole chat page or transcript: even a nominally horizontal responder can steal native wheel/touch scrolling. The drawer's optional close swipe is scoped to the drawer itself; open it with the floating menu control. Test touch paths with CDP `Input.dispatchTouchEvent`, and retain `overscroll-behavior-x: none` in `app/_layout.tsx` plus `touchAction: 'pan-y'` on bounded scrollers.
- Cross-origin gateway mocks need in-page `fetch` patching (`page.evaluateOnNewDocument`); network-level request interception fails CORS preflights.
- Expo Router keeps a collapsed (0x0) copy of a screen mounted next to the visible one, so `page.locator(sel).click()` can bind to the hidden copy and then time out after 30s waiting for a box that never appears. Click through `clickRendered` from `tests/helpers/web-server.js`, which filters by bounding box and still issues a real mouse click.
- Gateway URLs are configuration; never embed private runner addresses in app or test code.

## Provider-native Chat

`src/services/MagiConversation.ts` is the wire/revision adapter and `MagiConversationSession.ts` owns the one reactive human thread. Chat and Voice use only `/api/v1/magi/*` plus `magi_messages` socket events. Do not introduce target switching, worker/pane history, process-output parsing, transport flags, text matching, optimistic counting, or prompt-boundary inference.

Startup may render a validated principal-qualified cache while Gateway is unavailable. A successful list is authoritative and prunes/replaces it; canonical timestamps and monotonic revisions win. Logout, expiry, revocation, `401`, and principal change clear the conversation before protected remount. Read `../docs/native-chat-architecture.md` before changing this path.

## Voice input

`src/input/VoiceInputAdapter.ts` (`useVoiceInputAdapter`) is the one seam for microphone capture: it wraps `expo-audio` recording, exposes live `amplitude` (0-1, ~100ms cadence) for waveform UI, and on web also drives the Web Speech API for interim transcript callbacks. Pair `capture.stop()` with `transcribeVoiceAudio()` from `src/api/client.ts`, then submit the text through the same Native Magi API and conversation as typed input. Test it in headless Chrome with fake media-device/UI flags.

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
