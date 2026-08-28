# Magistrate MVP Wireframe

This is a bare-bones standalone HTML wireframe for evaluating the finalized MVP design and click path. It does not require the app backend.

## Open Locally

Open `docs/wireframes/mvp/index.html` directly in a browser.

Optional static server:

```sh
cd docs/wireframes/mvp
python3 -m http.server 4173
```

Then open `http://127.0.0.1:4173/`.

## Covered Flow

- Firstmate chat as the default landing surface.
- Mobile/desktop drawer with Attention, Fleet Summary, Recent Activity, Connections, and Settings.
- Attention brief with Approve, Reject, Dismiss, and PR review paths.
- Fleet expansion and individual agent chat.
- Continuous conversational voice inside chat plus optional immersive Voice Call mode.
- Connections health, authorization required, OAuth failure, and permission denied states.
- Settings appearance/background selection with automatic, built-in, and custom-placeholder states.
- Empty, loading, offline, reconnecting, blocked agent, unread message, and permission denied demo states.

All sample content is static demo content for wireframe review only. It is intentionally marked as demo state and must not be treated as production data.

## Integrity Check

```sh
node docs/wireframes/mvp/check-wireframe.js
```

The check verifies the entry file, referenced local assets, and internal navigation targets.
