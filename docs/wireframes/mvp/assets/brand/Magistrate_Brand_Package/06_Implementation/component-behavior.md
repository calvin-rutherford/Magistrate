# Product behavior specification

## Core visual rule

- **Calm at rest. Spectral when alive.** This is the governing behavior rule.
- Ordinary use is approximately 90-95% monochrome and neutral.
- Spectral color communicates system activity; it is not general decoration.
- Dormant states are quiet and trustworthy. Listening receives subtle cyan energy. Thinking and executing may add blue-violet. Full green-to-magenta spectrum is reserved for immersive voice, the organic active mark, significant activation, and brand imagery.
- Keep green, amber, and red semantic: success/healthy, warning/waiting, destructive/rejected/failure.

## Persistent Firstmate relationship

- Firstmate is the primary intelligence the user normally addresses.
- Do not expose arbitrary New Chat or disposable-thread controls.
- Restart preserves the visible conversation and reconnects Firstmate.
- While reconnecting, disable sending and state whether queued messages will send when the connection returns.
- End Connection requires a confirmation sheet.
- Spawned agents open their own real, durable conversations using the same conversation language as Firstmate.

## Canonical conversation

- The conversation is the center of gravity.
- Use natural prose for ordinary assistant responses and cards only for actions such as approve, reject, review, open, retry, or dismiss.
- Do not normally expose terminal output, shell commands, tool calls, code-edit logs, internal traces, or reasoning traces.
- The composer remains familiar: attachment, text input, microphone, send.
- The header shows current agent identity and only actually supported harness/model and reasoning-strength selectors.
- Support long history through virtualization or pagination. When away from the bottom, show a down-arrow or a `2 new` jump-to-latest control instead of moving the scroll position.
- Realtime messages must not steal scroll position from someone reading earlier content.

## Conversational voice - default

- Voice activates inside the current Firstmate or agent conversation.
- Keep conversation history, live transcription, assistant responses, composer, current agent/context, and voice state visible.
- The microphone may activate real amplitude peaks, restrained cyan/blue-violet illumination, and explicit listening/thinking/speaking labels.
- Continuous loop: listening -> thinking -> speaking -> listening.
- Support interruption/barge-in where the platform allows it.

## Immersive voice / call mode

- Preserve the organic full-spectrum triangle as an optional immersive mode.
- Entry points may include a dedicated Voice Call action, Siri, the Action Button, or explicit expansion from conversational voice.
- Show the organic active mark, live waveform, minimal conversation controls, and End Conversation.
- Return to the exact prior conversation, scroll, drawer, and composer state when closed.

## Drawer hierarchy

1. Attention
2. Fleet Summary
3. Recent Activity
4. Connections
5. Settings

- Use minimal monochrome line icons.
- Attention contains only items requiring awareness or judgment.
- Fleet Summary is expandable and lists Firstmate first, then spawned agents and their states. Selecting an agent opens its actual conversation.
- Recent Activity shows meaningful outcomes, not every internal tool operation.
- Connections is the authenticated integrations area; never represent OAuth state as a fake toggle.

## Attention and fleet

- Approval and PR items use compact restrained sheets/cards.
- Cards must name the requesting agent, the proposed action or review target, relevant health/test state, and available human actions.
- Agent conversations support real history, current status, unread state, realtime messages, scrolling, and jump-to-latest.
- Crew remains visible but subordinate to Firstmate.

## Connections

- Support GitHub, Jira, Microsoft Teams, and future authenticated services.
- Connection UI accommodates OAuth connection, account identity, available capabilities, reconnect, disconnect, authorization required, and health.
- Do not imply access that has not been granted or fabricate connection status.

## Backgrounds, themes, and glass

- Support Automatic, Built-in, and Custom background modes, including time-of-day and weather-aware environments where available.
- Custom images must persist durably; never depend on a temporary picker URI.
- Place an adaptive dim, blur, or gradient layer between imagery and content.
- Glass is a readability surface, not decoration.
- Light and dark environments have identical hierarchy, equivalent spectral rules, equal legibility, and no dark-only components.

## Mobile stability

- Prevent viewport zoom when chat receives focus.
- The software keyboard must not scale the layout or dislodge the composer.
- Prevent accidental horizontal scrolling and desktop layouts compressed onto phones.
- Respect safe areas; keep the composer stable; convert the drawer to a mobile drawer/sheet.
- Preserve readable, one-handed operation.

## Future interface compatibility

- Preserve the hierarchy Human -> Magistrate -> Firstmate / connected systems across iPhone, desktop, AR glasses, voice-only, headset, and wearable contexts.
- Do not make the brand dependent on phone-shaped chrome.

## Symbol behavior

- Keep one inverted triangle and one mathematically centered spiral.
- Treat the mark as an intelligence-state indicator: dormant, attentive, listening, reasoning, executing.
- Surround it with technical, precise, contemporary visual language; do not amplify occult, ritualistic, mystical, or esoteric associations.
