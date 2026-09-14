# Provider-native Magi Chat

Provider-native Magi Chat is the sole supported human-conversation path. It is
not selected by a rollout or rollback flag.

## Conversation flow

```text
Chat or Voice
  -> POST /api/v1/magi/messages
  -> MagiChatService reserves one owner-scoped user/assistant pair
  -> configured provider completion (with the closed Firstmate objective tool)
  -> exact final assistant content in magi_messages
  -> HTTP history/replay and magi_messages realtime events
```

`magi_conversations` and `magi_messages` are the current conversation store.
The authenticated principal supplies ownership; `client_message_id` provides
idempotency. The client reconciles canonical IDs and monotonic revisions through
`MagiConversation.ts` and keeps the one principal-qualified reactive thread in
`MagiConversationSession.ts`.

Voice is an input adapter for this same flow. Transcribed text is submitted to
`/api/v1/magi/messages`; Voice has no independent move or conversation API.

## Supported transport

Human conversation uses only:

- `POST /api/v1/magi/messages`;
- current, identified, and replay reads below `/api/v1/magi/conversations/*`;
- cancellation by native client-message ID; and
- `magi_messages` events on `/api/v1/events`.

The same socket may deliver structured `activity_records`. It does not deliver
terminal output, worker/pane history, selectable conversation targets, or
legacy captain events.

The retired captain prompt/output, terminal history, generic conversation,
Voice move, Pi ownership, and terminal-parser paths are not registered as human
conversation APIs. Historical database tables and unrelated deployment
compatibility infrastructure remain untouched until separately approved work.

## Separate execution surfaces

Structured Firstmate execution events, decisions, Activity, Attention,
evidence, notifications, runtime provisioning, Fleet projections, and explicit
agent controls remain separate from human Chat. Their presence does not create
an alternate conversation transport, and Native Magi reads do not consult
Herdr or terminal state.

`gateway/tests/test_native_chat_architecture.py` enforces the executable route
boundary and verifies that structured execution and non-chat agent controls
remain registered. Native Gateway and rendered browser behavior are covered by
`gateway/tests/test_magi_native_chat.py` and
`frontend/tests/native-chat.web.test.js`.
