# Magistrate AI chat formatting and information-design scout

**Audience:** Captain, product/design, and the separate implementation worker
**Scope:** ChatGPT/OpenAI and Claude/Anthropic patterns; Magistrate gap analysis and implementation plan
**Access date:** 2026-08-31 (UTC)
**Status:** Research only. No product code, device claims, PR, merge, or implementation authorization is included.

## Executive summary

Magistrate should borrow the *information architecture* that makes modern AI chats legible—clear role hierarchy, readable rich text, progressive disclosure for work, stable streaming, and recoverable failure—without copying ChatGPT or Claude's visual identity. Its brand should remain a quiet, traditional message thread over the existing glass/command surface: opaque cyan user bubbles, calm unboxed agent prose, restrained violet/cyan state accents, and Firstmate-assigned fleet names.

The most important distinction is between **conversation**, **execution evidence**, and **transport metadata**:

- Conversation is what the captain and agent said. It gets the largest type and the least chrome.
- Execution evidence is a compact, typed preview (tool name, short action, result state, artifact/source link) that is hidden by default and expandable when useful.
- Transport metadata includes Herdr/Pi markers, JSON-RPC envelopes, terminal chrome, model/provider internals, IDs, and raw output. It must never be rendered as prose.

The current implementation already has a good calm baseline: user/assistant alignment, opaque user bubbles, 17 px/26 px conversation type, a bounded scrollback strategy, local message persistence, live WebSocket plus polling fallback, a glass latest control, attachments, retry for failed sends, optional compact tool calls, and mobile drawer gestures. It does **not** yet render Markdown, code blocks, links/citations, timestamps, a real assistant streaming state, structured tool cards, assistant retry/regenerate actions, or typed message-level error states.

### Priority recommendation

1. **Next safe web/shared implementation PR:** introduce a small, allowlisted rich-text renderer; add local user timestamps with accessible full-date labels; add stable assistant action affordances; make the thinking/streaming/error states explicit; and preserve the current parser and no-metadata boundary.
2. **Next backend contract:** add normalized message IDs, server timestamps, message lifecycle/state, streaming deltas, structured tool events, and source/citation records. Do not infer these from Herdr snapshots.
3. **Native/device-dependent:** keyboard/inset behavior, Dynamic Type/font scaling, share/paste/document previews, accessibility services, and any background voice/push behavior need native testing. They are not reasons to expand the visual surface in this report.

No direct authenticated ChatGPT or Claude session was available to inspect. `chatgpt.com`, `claude.ai`, and OpenAI Help Center pages were blocked by access controls in this environment; Anthropic's public product page and both vendors' developer documentation were accessible. Accordingly, this report labels direct Magistrate source observations separately from vendor documentation/public product copy and third-party guidance. It makes no pixel-level claim about an authenticated ChatGPT or Claude build.

## Evidence and source table

**Evidence labels**

- **M — observed in Magistrate:** source/test behavior inspected in this checkout.
- **D — documented:** vendor or standards documentation; an API contract is not evidence of a consumer UI implementation.
- **P — public product copy:** vendor marketing/product page; useful for feature intent, not a pixel observation.
- **T — third-party guidance/research:** independent UX or accessibility guidance.
- **I — implementation inference:** recommendation derived from the evidence and Magistrate's approved requirements.

| Source | Type / access result | Relevant evidence | URL |
|---|---|---|---|
| Magistrate `frontend/app/(tabs)/chat.tsx` | M; local source, 2026-08-31 | User bubble, assistant text, tool preview, composer, attachment chips, latest control, drawer, voice affordance, action menu, delivery failure state, and styles. | Repository path |
| Magistrate `frontend/src/services/ChatHistory.ts` | M; local source, 2026-08-31 | Parser strips terminal chrome and classifies tool rows; `filterAgentHistory` hides tools by default; `toolCallPreview` deliberately removes command/path/provider detail. | Repository path |
| Magistrate `frontend/src/services/ConversationSession.ts` | M; local source, 2026-08-31 | Normalized local messages persist in AsyncStorage; `sentAt` is optional; server/Herdr replay has no reliable wall-clock time; `kind` is conversation/tool. | Repository path |
| Magistrate `frontend/src/realtime/socket.ts`, `frontend/src/api/client.ts` | M; local source, 2026-08-31 | Authenticated WebSocket is live-update transport with reconnect; HTTP history/polling remains fallback; prompt response can be synchronous, but there is no token-delta message contract. | Repository paths |
| OpenAI, **Streaming API responses** | D; HTTP 200, 2026-08-31 | Responses can be streamed as named events/deltas; the client must accumulate a final response and handle error events. This is API guidance, not proof of a ChatGPT web rendering detail. | https://developers.openai.com/api/docs/guides/streaming-responses |
| OpenAI, **File inputs** | D; HTTP 200, 2026-08-31 | Files are a first-class input, with supported formats and size/encoding constraints. The UI should distinguish attachment selection/upload from the eventual answer. | https://developers.openai.com/api/docs/guides/file-inputs |
| OpenAI, **Web search** | D; page accessible through current docs navigation, 2026-08-31 | Web search is a built-in tool with source/provenance implications; a client should keep source links distinct from ordinary prose. | https://developers.openai.com/api/docs/guides/tools-web-search |
| OpenAI, **File search** | D; page accessible through current docs navigation, 2026-08-31 | Retrieval is a tool, not conversational text; tool results need a compact, inspectable disclosure boundary. | https://developers.openai.com/api/docs/guides/tools-file-search |
| OpenAI, **Code interpreter** | D; page accessible through current docs navigation, 2026-08-31 | Code execution creates/uses files and outputs; output artifacts should be represented as artifacts or links, not dumped terminal output. | https://developers.openai.com/api/docs/guides/tools-code-interpreter |
| OpenAI, **ChatGPT Search** | D/P; official URL returned 403 in this environment | Official product reference for search/source behavior. Consult again in an authenticated/browser review before asserting exact current chips, source drawer, or citation placement. | https://openai.com/index/introducing-chatgpt-search/ |
| OpenAI, **ChatGPT release notes** | D/P; official Help Center returned 403 in this environment | Official change log, useful for date-bounding product behavior; not used here for unverified pixel claims. | https://help.openai.com/en/articles/6825453-chatgpt-release-notes |
| Anthropic, **Claude product overview** | P; HTTP 200, 2026-08-31 | Public copy explicitly positions conversation, file/image upload, Artifacts, voice mode, Projects, web research, and citations as adjacent capabilities. It does not document exact layout. | https://claude.com/product/overview |
| Anthropic, **Streaming messages** | D; HTTP 200 after redirect, 2026-08-31 | SSE has named events for content-block start/delta/stop, message deltas, pings, and errors; text, tool use, and thinking can stream. | https://platform.claude.com/docs/en/build-with-claude/streaming |
| Anthropic, **Thinking** | D; HTTP 200, 2026-08-31 | Thinking is a separate content block; current docs distinguish summarized display from omitted display and say displayed text is a summary, not raw chain of thought. | https://platform.claude.com/docs/en/build-with-claude/thinking |
| Anthropic, **Citations** | D; HTTP 200, 2026-08-31 | Citations attach to response text blocks, include cited passages and source locations, and provide a verifiable pointer rather than relying only on prompted citation prose. | https://platform.claude.com/docs/en/build-with-claude/citations |
| Anthropic, **Tool use with Claude** | D; HTTP 200, 2026-08-31 | Tool use is structured as tool-use blocks and stop reasons; client/server execution boundaries matter. Do not display raw tool protocol as assistant prose. | https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview |
| Anthropic, **Files API / PDF support** | D; HTTP 200, 2026-08-31 | Files can be uploaded/referenced; PDF processing supports text and visual content within documented limits. Attachment UX should surface filename/type/status and avoid promising unsupported processing. | https://platform.claude.com/docs/en/build-with-claude/files ; https://platform.claude.com/docs/en/build-with-claude/pdf-support |
| W3C WCAG 2.2, **Status Messages** | T/standards; HTTP 200, 2026-08-31 | Loading, completion, and error changes should be announced without moving focus; a polite live region is appropriate for noncritical stream/status changes. | https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html |
| W3C WCAG 2.2, **Contrast, Reflow, Keyboard, Target Size** | T/standards; HTTP 200, 2026-08-31 | Text/action contrast, 320 CSS px reflow, keyboard operation, and 24×24 CSS px minimum target guidance constrain the visual system and fixed composer. | https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html ; https://www.w3.org/WAI/WCAG22/Understanding/reflow.html ; https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html ; https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html |
| WAI-ARIA APG, **Alert pattern**; MDN `aria-live` | T/standards; HTTP 200, 2026-08-31 | Alerts are for important time-sensitive status; live updates should be scoped so a streaming answer does not cause screen-reader chatter for every token. | https://www.w3.org/WAI/ARIA/apg/patterns/alert/ ; https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live |
| Apple HIG, **Text fields** | T/platform guidance; HTTP 200, 2026-08-31 | Text input should remain legible, predictable, and usable with system keyboard behavior; native review is still required for insets, selection, and Dynamic Type. | https://developer.apple.com/design/human-interface-guidelines/text-fields |
| Nielsen Norman Group, **The User Experience of Chatbots** | T; HTTP 200, modified 2024-02-20 | Research describes the value of speed and low distraction, the need to be clear about bot capability, useful shortcuts, and an escape/recovery path; study predates current generative products, so it is directional rather than current product evidence. | https://www.nngroup.com/articles/chatbots/ |

## What the evidence says

### 1. Message hierarchy

**Documented/public vendor pattern.** OpenAI and Anthropic APIs both model the response as more than one undifferentiated string: streamed content, tool activity, files, source/citation data, and (for Anthropic) thinking blocks have different types. Claude's public product copy similarly places Projects, Artifacts, files, voice, research, and citations around the conversation rather than treating every output as plain chat text.

**Implication.** Modern AI interfaces are moving toward a primary answer plus secondary work/evidence surfaces. This is not a reason to make Magistrate look like an IDE. It is a reason to make the answer visually primary and give evidence a typed disclosure boundary.

**Magistrate decision.** Use three visual levels:

1. **Turn header/meta:** optional, low-contrast role/time/source label.
2. **Conversation body:** full 17 px-ish body text, readable paragraphs/lists, no bubble around assistant output.
3. **Evidence affordance:** compact tool/source/file/status row; never raw terminal prose.

### 2. Typography, spacing, and markdown

**Observed Magistrate baseline.** Conversation is `fontSize: 17`, `lineHeight: 26`; messages have a 16 px vertical list gap; user bubbles cap at 680 px and have 12×16 px padding with a 22 px radius; assistant messages are nearly unboxed; tool rows use 12/18 monospace. This is a sound calm starting point.

**Documented/public vendor pattern.** Vendor API docs assume text is segmented into content blocks and can include code, files, citations, or tool activity. They do not prescribe one typography scale. Public AI products generally use prose-first rendering with stronger heading/list/code contrast, but exact ChatGPT/Claude dimensions were not directly observed here.

**Recommendation.** Add a bounded renderer rather than a general HTML browser:

- Paragraphs: current body size/line height; 8–12 px paragraph separation.
- Headings: only `#` through `###`, a modest weight/size step, and margin before rather than oversized display type.
- Lists: preserve bullets/numbering and indentation; do not flatten terminal wrapping into prose.
- Emphasis/inline code: semantic weight and a subtle tinted code background; never use color alone.
- Code blocks: horizontally scrollable, selectable monospace, language label only when supplied, copy button with confirmation.
- Links: visibly underlined or otherwise distinct, with an accessible name; external links go through the existing external-link policy.
- Tables: defer complex responsive tables; first render a readable fallback or horizontal scroller with a caption.
- Raw HTML, scripts, images, arbitrary CSS, and untrusted URL schemes: reject/sanitize.

Do not use Markdown parsing as an excuse to reintroduce Herdr/Pi metadata. Parse only the normalized assistant `text` after the history boundary.

### 3. Citations and links

**Documented.** Anthropic's citations contract supplies exact cited text and source location. OpenAI documents web search as a built-in tool and its official ChatGPT Search material is the relevant product reference, but the exact UI was inaccessible during this run. Both point to a common design need: provenance should be inspectable and attached to the claim, not hidden in an undifferentiated footer.

**Recommendation for Magistrate.** Do not add decorative fake citations. Add a future normalized source shape such as `sourceId`, `title`, `url`, `publisher`, `retrievedAt`, and optional quoted passage/page/line. Render:

- inline numbered source markers only when the backend can associate them with a claim;
- a compact `Sources` disclosure below the answer, collapsed by default when there are more than two;
- a source row with title/domain and an explicit open action;
- a clear `Sources unavailable` or `Not verified` state rather than an empty citation affordance.

This requires backend data. A safe next PR can add a renderer behind a fixture/typed boundary, but must not invent source records from URLs found in arbitrary prose.

### 4. Tool-call previews and calm-vs-detail disclosure

**Documented.** Anthropic separates tool-use blocks from text and explains client/server execution. OpenAI documents built-in search, file search, code execution, and other tools as distinct calls. These contracts support showing *what happened* without showing protocol internals.

**Observed Magistrate.** Tools are opt-in through the single persisted key `magistrate.chat.show-tool-calls`. The parser drops chrome and `toolCallPreview` reduces a tool row to a label such as `Running…`, `Bash`, or `Read`. This is exactly the right safety boundary, but it is currently a text row rather than a typed card.

**Recommendation.** Keep tools hidden by default. When enabled, show a single compact row per meaningful operation:

- status icon/label: `Running`, `Complete`, `Failed`, or `Waiting for approval`;
- Firstmate-assigned agent name and short human-readable action;
- elapsed time only if a trustworthy event timestamp exists;
- one disclosure control for details/evidence;
- no raw command, filesystem path, provider/model, token count, Herdr glyph, or Pi chrome in the conversation view.

Tool output should not interrupt the answer's reading order. Group consecutive low-value calls into `Worked through 4 steps` with an expandable list. A consequential action must not be visually made to look complete merely because its tool call returned.

### 5. Thinking and progress states

**Documented.** Anthropic's current docs distinguish thinking blocks from final text, say that displayed thinking is a summary rather than raw chain of thought, and describe streaming content blocks. This is guidance for a model/API, not authorization to expose private reasoning. OpenAI's streaming docs similarly establish incremental events and error events without requiring the UI to show internal reasoning.

**Observed Magistrate.** `isThinking` changes the composer send button and a status area; there is no durable assistant placeholder, stream delta, progress phase, or cancel/stop control in the conversation itself.

**Recommendation.** Use a calm state machine:

- `queued`: user turn is visible; one status line says it is queued.
- `working`: one assistant placeholder, `Working…`, with an optional compact phase such as `Checking the fleet`; no chain-of-thought.
- `streaming`: assistant body grows in place; preserve scroll anchoring unless the captain is reading older content.
- `complete`: replace placeholder metadata with final actions.
- `failed`: retain the user turn and partial assistant text (if any), show a plain explanation and `Retry`; never silently discard.
- `cancelled`: label it explicitly and allow a new prompt.

Announce state transitions, not every token. For screen readers, use a polite live region with debounced summaries; mark the streaming body selectable but avoid continuously replacing its accessible node. A stop action is useful only once the backend can actually interrupt the corresponding run.

### 6. Streaming, scroll-to-latest, and unread state

**Documented.** Both vendor APIs define incremental streaming and named error/event boundaries. This supports a client-side append/update model rather than repeated whole-transcript replacement.

**Observed Magistrate.** The client has authenticated WebSocket reconnect and polling fallback, bounded history/cursors, a latest arrow, and a `hasNewMessages` state. Existing tests verify initial latest positioning and that incoming messages do not steal an older reading position.

**Recommendation.** Preserve those invariants and make them explicit:

- At bottom: append/update in place and follow the active answer.
- Away from bottom: do not jump; show a glass `New message` arrow/badge with count.
- During a stream away from bottom: update content in place but do not move the viewport.
- Tapping latest clears unread state and places the viewport at the final message bottom.
- On reconnect: reconcile by stable message/event IDs, not text equality; do not duplicate repeated legitimate prompts.
- Empty/transient Herdr snapshots are not conversation deletion.

A backend event ID and message ID are required for exact stream reconciliation; current local IDs are not enough across devices.

### 7. Attachments

**Documented/public.** OpenAI and Anthropic both treat files as first-class inputs, with provider-specific size/type/processing limits. Anthropic's PDF documentation makes visual/text capability and limits explicit. Claude's public overview presents upload as part of the conversation experience.

**Observed Magistrate.** The composer offers Photos and Files; chips show thumbnail/name/type/size/status; sends require descriptive text; uploads are authenticated, associated by message ID, and failed messages can be retried.

**Recommendation.** Keep the current attachment model. Improve only the information design:

- pending chip: filename + type + `Uploading…`;
- failed chip: specific reason + retry/remove, with focusable action;
- sent message: compact attachment row with an accessible open/download action;
- answer: do not imply the agent read a file unless the backend/provider confirms it;
- oversized/unsupported files: inline error next to the chip and a polite live announcement.

Image previews and document processing need native and backend confirmation before claiming parity across platforms.

### 8. Errors, retry, copy/edit/regenerate, and timestamps

**Documented/third-party.** Streaming APIs have explicit error events; NN/G's research emphasizes honest capability boundaries and recovery/escape paths; WCAG requires status changes to be conveyed accessibly without disruptive focus movement.

**Observed Magistrate.** User messages have sending/failed states and retry for attachment-related failures. Long-press opens Edit, Copy, Select text. There is no assistant Copy/Edit/Regenerate action set, no rendered timestamp, and no normalized server time.

**Recommendation.** Make actions role-appropriate and nonintrusive:

- user: timestamp, Edit, Copy, Select text; preserve opaque bubble;
- assistant: Copy, optionally Regenerate/Retry only when a run ID and retry semantics exist; do not offer Edit for agent prose;
- tool/source rows: Copy details or Open source only when structured data exists;
- copy: show a transient `Copied` confirmation and retain the accessible label;
- send failure: keep the original user turn, explain whether text, upload, auth, or service failed, and provide retry without duplicating the turn;
- partial answer failure: show `Response stopped` and offer retry from the original request, not a second hidden prompt;
- timestamps: render local user time in a compact `HH:mm`/locale-aware form; expose a full localized date/time to assistive technology. Agent timestamps stay absent until the server supplies trustworthy values.

`sentAt` already exists for local messages; rendering it is a safe shared implementation change. Do not fabricate times for Herdr replay.

### 9. Composer, fixed controls, and mobile behavior

**Observed Magistrate.** The composer is fixed within the canvas, rounded, supports attachment menu, model selection, mic, send/queue behavior, and a status line. The app has mobile visual-viewport handling, `touchAction: pan-y`, a full-screen-ish right swipe drawer, and tests protecting focus/zoom behavior.

**Public/documented.** Claude public copy identifies voice, files, and Projects as adjacent modes; Apple HIG and WCAG constrain readable inputs, focus, keyboard behavior, reflow, and target sizes. The vendors' API docs do not define mobile UI.

**Recommendation.** Do not add a second toolbar. Preserve the fixed composer and make its hierarchy explicit:

1. attachment button;
2. flexible multiline input;
3. model/control affordance;
4. mic;
5. send/stop.

Keep all visible text inputs at 16 px or larger on web to avoid mobile zoom (already tested). On narrow screens, attachment chips scroll horizontally, menus stay within the safe viewport, the keyboard never obscures the send action, and the composer grows only to a bounded number of lines before scrolling internally. The latest arrow must sit above the composer with sufficient inset and not be covered by the keyboard.

The right swipe should remain a drawer gesture, not a message action. Do not make horizontal code scrolling compete with the full-screen drawer gesture; code blocks need a local horizontal scroller that consumes horizontal movement once engaged.

### 10. Accessibility

Required semantics for the formatting system:

- conversation history has a meaningful label and a predictable reading order;
- each user/assistant turn has an accessible role/name, not only color/alignment;
- timestamps are supplementary text, not the only distinction between turns;
- streaming status uses a debounced polite live region; critical failures use an appropriate alert, not a permanent noisy region;
- every icon-only action has a name and visible focus state;
- action menus can be opened, navigated, and dismissed with keyboard/Escape and do not rely on long-press alone;
- links, code copy, source rows, attachment actions, latest arrow, and retry meet target-size/focus/contrast requirements;
- `prefers-reduced-motion` disables decorative stream/drawer animation while keeping state changes visible;
- Dynamic Type/font scaling does not clip message text or make the composer unusable on native;
- selectable text remains selectable after Markdown rendering, and code is not conveyed only through a color or monospace change.

## Magistrate gap analysis

| Area | Current evidence | Gap / risk | Priority / ownership |
|---|---|---|---|
| Role hierarchy | User right opaque bubble; assistant left unboxed text | No explicit role/time metadata; user/assistant distinction relies partly on alignment | Must-have; shared UI |
| User timestamps | `sentAt` retained for local turns but not rendered; replay time absent | Approved timestamp requirement is not met; fabricating server time would be worse | Must-have; shared UI + later backend time |
| Agent text | Plain `<Text>` | Markdown, lists, headings, links, code, tables, and safe URL handling absent | Must-have for useful AI output; shared UI |
| User text | Plain `<Text>` inside bubble | User Markdown is correctly not required; attachment summary is plain | Keep simple; shared UI |
| Code | Tool rows use monospace; assistant code does not | Long lines can overflow; no copy/language affordance | Must-have for implementation value; shared UI |
| Citations | No source model or renderer | Cannot truthfully render claim-level provenance | Should-have after backend contract |
| Tool calls | Parser filters; preference key; compact label | No typed status, grouping, result, or disclosure; raw source data still exists upstream | Must-have boundary; Should-have card; shared + backend |
| Thinking/progress | Composer thinking indicator/status only | No message-level lifecycle, stream phase, or stop semantics | Must-have state UI; backend for real stream/stop |
| Streaming | WebSocket/polling live updates, prompt response can be synchronous | No delta contract or stable event lifecycle; whole message replacement risk | Should-have; backend + shared |
| Scroll/unread | Bounded cursor history, latest arrow/new-message state and regression tests | Count/stream reconciliation and accessible announcement need explicit coverage | Must-have preserve; shared |
| Attachments | Authenticated upload, preview chips, association, retry | Agent receipt/processing confirmation is not exposed; file capability differs by provider | Must-have current boundary; backend for processing status |
| Links | Existing external URL utility elsewhere | Chat body links/citations are not rendered or safely allowlisted | Should-have; shared + security review |
| Errors/retry | User sending/failed and attachment retry | No assistant partial/error/retry contract; generic errors can be too vague | Must-have basic state; backend for run retry |
| Copy/edit | User long-press Edit/Copy/Select text | No assistant actions; long-press is not sufficient for keyboard/accessibility | Must-have action semantics; shared |
| Composer | Fixed rounded surface, attachments/model/mic/send, queue | Multiline/keyboard/inset and accessibility behavior need renderer regression tests | Must-have preserve; shared + native validation |
| Mobile drawer | Tested full-screen right swipe and drawer slide | Rich code scrolling and drawer gesture may conflict | Should-have; shared/mobile test |
| Fleet names | UI has Herdr display-name fallback/rename | Requirement says Firstmate-assigned fleet names; formatting must not invent provider/harness names | Must-have source-of-truth decision; backend/data contract |
| Metadata boundary | Parser strips known terminal chrome/tool details | Snapshot parsing remains heuristic; new harness output could leak | Must-have; parser fixtures plus real snapshot validation |
| Agent-only output | Agent history filtering hides tool rows by default | History contains role `user`; unclear whether server-replayed user rows should be shown in this product mode | Captain choice; do not silently change parser |

## Recommended Magistrate formatting system

### Visual grammar

- **Canvas:** retain the existing glass shell and generous bottom composer clearance.
- **User turn:** opaque cyan bubble, right aligned, max-width 680 px on wide screens, readable body text, one compact local timestamp below or inside the bubble. The timestamp should never reduce text contrast or make the bubble translucent.
- **Agent turn:** left aligned, unboxed, same readable body scale; optional small `Magi`/Firstmate-assigned agent label only when the target is not obvious. Avoid provider logos and model names in the thread.
- **System/evidence row:** muted, compact, full-width within the conversation column; a thin violet/cyan state accent, not a prominent card. Tool rows are opt-in and collapsible.
- **State:** one restrained animated indicator while active, static fallback for reduced motion, and a clear textual state for accessibility.
- **Actions:** appear on hover/focus on web and through an accessible menu on touch/long-press. Do not permanently add a row of buttons under every message.
- **Sources:** claim markers and a collapsed source list only when backed by structured records.
- **Code:** dark/light surface that meets contrast, rounded 10–12 px corners, horizontal scroll, copy action, no full-screen takeover.

### Content contract (proposed, not yet an implementation authorization)

```text
ConversationMessage
  id: stable message id
  role: user | assistant
  text: markdown text after parser boundary
  sentAt: trusted timestamp | absent
  delivery: queued | sending | sent | failed | cancelled
  runId: optional stable execution id
  agentName: Firstmate-assigned display name | absent
  attachments: normalized attachment records
  sources: normalized citation/source records | absent
  progress: queued | working | streaming | complete | failed | cancelled
  toolEvents: compact structured evidence | absent
```

A backend should emit events with a stable `eventId`, `messageId`, `runId`, `kind`, and monotonic sequence. The client should be able to replay/reconcile without comparing text. `sources`, `toolEvents`, and `progress` must be absent rather than guessed when the server cannot provide them.

### Disclosure rules

| Detail | Default | Reveal when |
|---|---|---|
| Final agent prose | Visible | Always |
| User local timestamp | Visible, low contrast | Always; full value in accessibility label |
| Assistant server timestamp | Hidden until trustworthy | Backend provides it and captain choice approves |
| Tool name/status | Hidden by default | Captain enables Show tool calls; consequential status may be visible as a brief state |
| Tool arguments/raw command | Never in normal transcript | Only in a dedicated diagnostics surface, if ever approved |
| Thinking summary | Hidden | Only if the backend explicitly labels it as a safe summary and captain approves; never expose raw chain of thought |
| Sources/citations | Compact marker/list | Only with structured provenance |
| Model/provider/harness | Hidden | Existing execution settings/diagnostics, not message prose |
| Attachment filename/type | Compact row | When attached or sent; processing result only when confirmed |
| Queue/reconnect status | Brief status | While active or when it affects delivery |

## Prioritized implementation plan

### Must-have: safe for the next shared/web implementation PR

1. **Add a safe assistant Markdown renderer.** Support paragraphs, headings, lists, emphasis, inline code, fenced code, and safe links. Keep user text plain unless product chooses otherwise. Add tests for terminal-looking strings, untrusted HTML/URL schemes, long code, lists, and empty/partial Markdown during streaming.
2. **Render local user timestamps.** Use `sentAt` only when present; locale-aware short time visually and full localized date/time in accessibility text. Leave Herdr-discovered messages undated. Update exact-text tests to assert the message body separately from metadata.
3. **Make message actions accessible and role-specific.** Keep Edit/Copy/Select for user turns; add Copy for assistant turns; expose a keyboard-accessible menu and visible focus state in addition to long-press. Defer Regenerate until run IDs/retry semantics exist.
4. **Formalize state presentation.** Add a message-level placeholder/status component for queued, working, streaming, complete, failed, and cancelled states. It may initially be driven by existing `isThinking` and send errors, but must not claim token streaming or backend cancellation.
5. **Preserve and test the metadata firewall.** Keep `filterAgentHistory` default-off, parser chrome/tool filtering, no Herdr/Pi labels, and no arbitrary JSON rendering. Add regression cases for new harness rows and a real snapshot check as required by project memory.
6. **Harden latest/unread behavior around rich content.** Test stream-like content growth, code blocks, links, images/attachments, and reduced motion without losing the existing older-reading position.
7. **Accessibility pass.** Add labels/live-region behavior, focus handling, reduced motion, contrast checks, target-size checks, and keyboard/Escape actions to the shared/web tests.

### Should-have: next slices after the safe PR

1. **Structured tool preview cards.** Requires backend event schema and stable run/event IDs. Group repetitive low-value calls; show only short action/status/result labels.
2. **Structured sources/citations.** Requires a backend source contract and trust policy. Render inline markers plus a collapsed source list; never parse citations from prose heuristically.
3. **Real streaming deltas.** Requires gateway/Herdr/provider support, ordering/replay semantics, partial response persistence, and error/cancel behavior. Keep polling fallback.
4. **Assistant regenerate/retry.** Requires idempotent run retry and clear cost/side-effect semantics. Do not map it to blindly resending a prompt that may have performed work.
5. **Attachment processing state.** Requires provider capability/receipt data; distinguish uploaded, accepted, processed, unsupported, and failed.
6. **Firstmate fleet-name contract.** Make the assigned display name a typed data field from Firstmate and keep rename behavior consistent with that authority. No formatting component should derive names from harness/provider/system labels.
7. **Responsive source/code surfaces.** Add a local code scroller and source drawer that cannot hijack the full-screen right swipe; verify with touch tests.

### Defer

- Pixel cloning of ChatGPT or Claude, including their fonts, logos, colors, exact chips, or interaction choreography.
- Exposing raw thinking/chain-of-thought, terminal transcripts, Herdr/Pi metadata, provider/model routing, or token accounting in conversation.
- Claim-level citations without backend provenance.
- Background streaming, offline send queues that can execute later, push-driven conversational updates, background voice, Siri/App Intent, Action Button, or Bluetooth/audio-route claims.
- Rich interactive artifacts, arbitrary HTML/React rendering, spreadsheet/table editors, image generation canvases, or a second workspace beside the chat.
- Assistant Edit, branching conversation trees, multi-version answer history, or Regenerate until server semantics are explicit.
- Full server timestamps for replayed terminal history until the source supplies trustworthy event time.
- Replacing the current bounded live chat with Herdr's unbounded terminal scrollback.

## Safe next PR vs native/device vs backend support

| Change | Safe in shared/web PR? | Native/device support or validation | Backend support |
|---|---:|---|---|
| Markdown/code/link renderer with sanitization | Yes | Native selectable text and font scaling validation | No, unless server sends richer blocks |
| User local timestamp | Yes when `sentAt` exists | Locale, Dynamic Type, VoiceOver/TalkBack check | Needed for server/replayed timestamps |
| Assistant Copy and accessible action menu | Yes | Long-press, rotor/context-menu behavior validation | No |
| Visible working/failed placeholder | Yes as honest local state | Keyboard/animation/accessibility validation | Needed for true run lifecycle |
| Token-by-token streaming | No | Rendering/performance/device network testing | Yes: delta/event IDs, ordering, persistence |
| Stop/cancel generation | No | Native interruption semantics later | Yes: run cancellation endpoint/event |
| Tool preview card | Fixture-only UI can be built | Touch disclosure validation | Yes for trustworthy structured event data |
| Citations/source drawer | Fixture-only UI can be built | External link/share/open behavior | Yes for provenance and retrieval timestamps |
| Attachment chip/status polish | Yes | Photo/file permissions, previews, download/share | Yes for processing/receipt state |
| Full-screen right swipe | Preserve existing code; no redesign | Real touch/back gesture matrix | No |
| Voice/background/push updates | No claim | Physical iPhone, audio route, APNs/FCM, background testing | Durable notification/streaming support |
| Firstmate-assigned fleet names | UI can consume a field | Native display only | Yes: authoritative name in fleet schema |

## Acceptance criteria and test ideas

### Content and hierarchy

- A message containing paragraphs, `#`/`##` headings, ordered/unordered lists, inline code, a fenced code block, and a safe external link renders with no raw Markdown delimiters and no clipped content.
- A string containing Herdr/Pi markers, JSON-RPC envelopes, terminal chrome, tool rows, or a fake `javascript:` link remains filtered or escaped and never becomes an assistant prose message.
- User text remains an opaque bubble and does not accidentally render agent Markdown or tool metadata.
- Assistant prose is visually primary; tool/source details do not alter the default conversation when `showToolCalls` is false.
- Long code can be selected/copied and horizontally scrolled without causing horizontal page overflow or opening the drawer.

### Time, delivery, and state

- A local user message displays a short local timestamp and exposes a full date/time label; a Herdr-replayed message with no `sentAt` has no invented timestamp.
- Sending, queued, working, streaming-like update, complete, failed, and cancelled states have stable accessible names and no duplicate user turn.
- A failed send keeps its original text/attachments and retry is idempotent from the UI perspective.
- An assistant partial/error state is visually distinct, retained for diagnosis, and offers no misleading “done” status.
- Copy gives a visible and accessible `Copied` confirmation without moving focus unexpectedly.

### Live updates and scroll

- While at the bottom, a growing assistant response follows the latest content.
- While reading older content, a new message or growing response does not change `scrollTop`; a glass `New message`/`Latest` control appears and works by keyboard/touch.
- Reconnect plus polling of the same event does not duplicate it; repeated identical user text in separate turns remains distinct.
- A transient empty snapshot does not clear the visible thread.
- Reduced motion removes decorative movement but retains all state and unread cues.

### Attachments and sources

- Attachment chips show uploading/sent/failed status, filename, type, and size without claiming that a provider processed the file.
- Invalid, oversized, or unavailable files produce a local actionable error and do not leak a local path, token, or raw gateway response.
- A fixture source record renders a claim marker and source disclosure; absent source data renders no fake marker.
- Opening a source uses the existing external-link policy and has a meaningful accessible label.

### Mobile and accessibility

- At 320 CSS px / a 390×667-class mobile viewport, the composer remains usable, text inputs remain at least 16 px on web, and keyboard/visual viewport changes do not obscure send/latest controls.
- Every icon-only control has an accessible name; action menus work with keyboard, Escape, focus, and touch/long-press.
- Contrast and focus checks pass in both light and dark themes; color is not the only status signal.
- Screen-reader output announces `Working`, completion, and failure once per transition rather than once per streamed token.
- Native test matrix covers Dynamic Type, VoiceOver/TalkBack, keyboard insets, file/photo permission denial, reduced motion, and back/swipe gestures before calling the feature device-ready.

## Unresolved captain choices

These are genuine product/contract choices above the implementation worker. Preserve the captain-hold gate; do not resolve them by silently choosing in code.

1. **Agent-only output definition:** Should the visible thread show locally authored captain turns plus agent replies, while excluding all server-replayed user rows; or should normalized conversation history show both roles when available? The approved direction says agent-only output in the sense of no raw harness/metadata output, but the exact history policy needs one sentence of authority.
2. **Timestamp policy:** Approve local user `HH:mm` plus accessible full time now, with no timestamps for undated agent history; or require a backend server-time contract before any timestamp appears? The former is safe and honors the current `sentAt` field.
3. **Tool default and consequential states:** Keep all tool previews opt-in as today, or show a minimal `Waiting for approval`/`Failed` status even when tools are hidden? This affects the meaning of “agent-only output” and attention safety.
4. **Thinking disclosure:** Keep all thinking summaries hidden, or allow a provider-labelled safe summary under an explicit preference? The recommendation is hidden by default and never raw reasoning.
5. **Citation scope:** Should Magistrate support web-source citations first, attachment/document citations first, or both through one provenance model? Do not build a vendor-specific citation UI before this choice.
6. **Regenerate semantics:** Is retry allowed only for a failed, non-side-effecting run, or may the captain request a new answer after completion? A synchronous prompt resend is not a safe substitute for a run-aware regenerate action.
7. **Firstmate fleet-name authority:** Is the Firstmate-assigned name immutable in chat, or can the existing rename control update the authoritative name? The formatter should consume one source of truth either way.
8. **Native parity gate:** Which acceptance artifact is required before calling mobile formatting complete: simulator test, physical iPhone matrix, or both? Web screenshots must not substitute for device evidence.

## Bottom line

Magistrate does not need a busier AI workspace. It needs a more reliable reading surface: answer first, evidence on demand, state that is honest, provenance that is real, and recovery that preserves the captain's work. The next implementation worker can safely improve Markdown, timestamps, action accessibility, state presentation, and tests without changing the brand or backend architecture. Streaming, structured tools, citations, assistant regeneration, and native/background behavior should wait for the contracts and evidence that make those affordances truthful.
