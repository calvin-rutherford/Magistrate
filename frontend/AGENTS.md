# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v57.0.0/ before writing any code.

## Web tests

Run the suites via the `test:*` scripts in `package.json`; each spawns its own Expo web server and drives headless Chrome through puppeteer-core.

Sharp edges when driving the web build in headless Chrome:
- Expo's development-only `#error-toast` has a zero-sized box but can win hit-testing near the viewport bottom, silently swallowing clicks on the composer or drawer footer. Disable its pointer events first (see `tests/chat-terminal.web.test.js`).
- React Native `PanResponder` gestures do not fire from synthetic mouse drags; dispatch real touch events via the CDP `Input.dispatchTouchEvent` command instead.
- Cross-origin gateway mocks need in-page `fetch` patching (`page.evaluateOnNewDocument`); network-level request interception fails CORS preflights.

## Backend model selection contract

`POST /api/v1/captain/prompt` requires `harness` and `model` together or neither; omitting both keeps the backend's current session selection (see `gateway/app/main.py`). Offer variants from `GET /api/v1/execution/capabilities` only.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
