# Native-shell touch-up — visual evidence

Captured with `node frontend/scripts/capture-brand-evidence.js` (headless Chrome via
`puppeteer-core`; `chrome-devtools-axi` cannot write files on this box — see
`frontend/AGENTS.md`). Viewport is an iPhone-class 390×844 @2x with the gateway mocked.

| Shot | What it shows |
| --- | --- |
| `01-resting-magi-iphone.png` | Resting Magi surface: floating menu control, `Magi · Automatic` identity, one contextual control, centred mark + greeting, floating pill composer. |
| `02-drawer-iphone.png` | Drawer as a layer over the same screen — the chat surface stays visible, inset and rounded behind it. |
| `03-drawer-fleet-iphone.png` | Fleet in place of a conventional chat list. |
| `04-drawer-attention-iphone.png` | Attention as a first-class, calm entry. |
| `05-settings-sheet-iphone.png` | Settings as a native sheet: grabber, large rounded top, Done, grouped rows. |
| `06-appearance-iphone.png` | Appearance: theme, environment thumbnails, custom-background upload. |

## Deferred

The full side-by-side comparison against the supplied Gemini/ChatGPT references
(prompt section 29), the conversation/execution-sheet/desktop captures, and the
physical-iPhone rows of the section 28 acceptance test are **not** included here.
The capture script already covers the remaining shots (`07`–`11`); re-run it to
produce them. Physical-device rows remain with DEAT-001.

## Known follow-up

`NotificationPermissionPrompt` renders above the shell and covers the top of the
resting surface and the drawer (visible in `01` and `02`). It predates this pass
and is unchanged here; it should become a calm inline entry rather than a card
over the resting Magi surface.
