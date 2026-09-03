# Magi structured responses v1

**Status:** implemented in Magistrate; upstream semantic producer integration is explicitly pending

**Contracts:** `magi.response.v1`, `magi.event.v1`
**Canonical endpoint:** `POST /api/v1/conversations/captain/events`

This is an additive path for first-class Magi response semantics. It does not replace Herdr process/terminal control, the canonical conversation record, or the hardened terminal fallback. It never derives JSON from Markdown or terminal prose.

## End-to-end ownership

```text
semantic producer
  -> authenticated magi.event.v1
  -> strict Gateway validation and revision ledger
  -> canonical conversation row (structured document + plain text)
  -> existing HTTP / WebSocket canonical delivery
  -> independent client validation
  -> native React Native block renderer

Herdr terminal snapshot
  -> existing classifier/boundary firewall
  -> canonical plain-text fallback only
```

A captain prompt now reserves both a `turn_id` and one `assistant_message_id`. Every semantic event for that turn must use those exact ids. The prompt response exposes both under `conversation`.

Resolution order is deliberate:

1. a validated structured event/document;
2. the same canonical row's bounded plain-text projection for clients that do not understand v1;
3. the existing terminal-derived text fallback when no semantic event exists.

Once `assistant.started` is accepted, terminal ingestion no longer writes that turn. A late synchronous provider reply cannot overwrite it either. Existing turns and deployments without a semantic producer behave as before.

## `magi.response.v1`

The contract is a closed, bounded document. Its authoritative Pydantic definition is in [`gateway/app/contracts.py`](../gateway/app/contracts.py); the independently implemented client validator is in [`frontend/src/services/MagiResponse.ts`](../frontend/src/services/MagiResponse.ts).

```json
{
  "schema_version": "magi.response.v1",
  "blocks": [
    {
      "type": "heading",
      "block_id": "summary",
      "level": 1,
      "content": [{ "type": "text", "text": "Deployment summary" }]
    },
    {
      "type": "paragraph",
      "block_id": "result",
      "content": [
        { "type": "strong", "text": "Healthy." },
        { "type": "text", "text": " Read the " },
        { "type": "link", "text": "runbook", "url": "https://example.com/runbook" },
        { "type": "text", "text": " or use " },
        { "type": "inline_code", "text": "status --all" },
        { "type": "text", "text": "." }
      ]
    },
    {
      "type": "list",
      "block_id": "checks",
      "style": "ordered",
      "items": [
        [{ "type": "text", "text": "API checks passed." }],
        [{ "type": "emphasis", "text": "No action required." }]
      ]
    },
    {
      "type": "code",
      "block_id": "example",
      "language": "bash",
      "code": "status --all"
    },
    {
      "type": "quote",
      "block_id": "note",
      "content": [{ "type": "text", "text": "Terminal text remains a fallback." }]
    },
    { "type": "divider", "block_id": "end" }
  ],
  "actions": []
}
```

Allowed blocks are `paragraph`, `heading` (levels 1–4), `list`, `code`, `quote`, and `divider`. Allowed inline nodes are `text`, `strong`, `emphasis`, `inline_code`, and `link`. The only reserved action type is `open_url`; v1 stores and validates actions but does **not** turn them into executable controls.

There is no HTML node, component name, style object, script, image, tool invocation, or arbitrary property bag. Literal HTML in a text node remains inert text.

### Bounds and URL policy

- document: at most 256 KiB encoded JSON;
- aggregate text/link/action characters: at most 200,000;
- blocks: 1–128, with unique safe `block_id` values;
- inline nodes per group: 1–128;
- aggregate inline nodes per document: at most 1,024;
- list items: 1–100;
- code: at most 65,536 characters;
- actions: at most 16, with unique safe `action_id` values;
- links: absolute `http` or `https`, at most 2,048 characters, no credentials, whitespace, controls, or backslashes.

Unknown versions, node types, fields, invalid ids, invalid scalar types or
Unicode, duplicate ids, unsafe URLs, excessive aggregate fan-out, and oversize
content are rejected. Pydantic coercion is disabled for these contracts.

## `magi.event.v1`

Every event contains:

```json
{
  "schema_version": "magi.event.v1",
  "event_type": "assistant.started",
  "event_id": "evt_01",
  "turn_id": "ct_...",
  "message_id": "cm_...",
  "revision": 1
}
```

Lifecycle variants are a discriminated union:

| `event_type` | Additional fields | Meaning |
| --- | --- | --- |
| `assistant.started` | none | Opens the semantic stream; must be revision 1. |
| `assistant.block.upsert` | `block`, `block_index` | Appends a new block at the next index or updates the same id at its stable index. |
| `assistant.block.remove` | `block_id` | Explicit producer correction only. Omission never deletes a block. |
| `assistant.completed` | `response` | Supplies the complete authoritative `magi.response.v1` document. |
| `assistant.failed` | `error_code`, optional `error_message` | Ends the stream as failed. |
| `assistant.cancelled` | none | Ends the stream as cancelled. |

Ordering and identity rules:

- one captain turn reserves one assistant message id;
- revisions start at 1 and increase by exactly one;
- `(turn_id, revision)` and `event_id` are durable uniqueness boundaries;
- replaying the same event id and payload is a no-op and returns `status: duplicate`;
- reusing an event id for different content, skipping/reusing a revision, changing the message id, or writing after a terminal event returns HTTP 409;
- completion is authoritative; no later semantic or terminal revision can mutate it;
- removing a missing block or the final remaining block is rejected.

The event body requires an authenticated session with `command` scope and a declared body length. Malformed contracts return 422, unknown turns return 404, ordering/identity conflicts return 409, and oversize bodies return 413.

## Canonical storage and delivery

[`gateway/app/db.py`](../gateway/app/db.py) migrates existing SQLite databases additively:

- `conversation_turns.assistant_message_id` reserves identity before output exists;
- `conversation_messages.content_source` is `structured` or `terminal-fallback` for assistant prose;
- `conversation_messages.structured_content_json` stores the validated canonical document;
- `conversation_messages.structured_revision` stores the producer revision represented by that document;
- `magi_response_events` stores accepted event identity, order, type, and payload hash for durable replay protection.

Existing raw `text`, terminal `source`, messages, turns, and unrelated tables are not rewritten. Structured rows keep a plain-text projection in `text` for accessibility, export, and old clients; this projection is derived from an already validated document and is not Markdown inference.

The existing `conversation.v1` HTTP and `conversation_messages` WebSocket payloads add these fields to structured assistant rows:

```json
{
  "content_source": "structured",
  "structured_revision": 4,
  "structured_content": {
    "schema_version": "magi.response.v1",
    "blocks": [{ "type": "divider", "block_id": "end" }],
    "actions": []
  }
}
```

Poll and socket delivery continue to reconcile by canonical message id and revision, so duplicate transports update one row.

## Native rendering and fallback

[`frontend/src/components/StructuredAssistantMessage.tsx`](../frontend/src/components/StructuredAssistantMessage.tsx) maps validated blocks directly to React Native `View`, `Text`, `ScrollView`, and `Pressable` components. It does not serialize the document to Markdown and does not use web-only HTML rendering. Code remains horizontally scrollable and copyable; links pass through the conservative external URL opener.

`CanonicalConversation.ts` independently validates every HTTP/WebSocket document. `ConversationSession.ts` repeats validation when restoring cached JSON. A malformed, unknown, or unsupported structured document is discarded without crashing, while its bounded canonical `text` is rendered through the existing `SafeMarkdown` fallback. Old text-only and new structured rows can coexist in one history.

## Upstream Firstmate / Herdr / harness seam

Investigation was performed against the installed runtime on 2026-09-03:

- **Herdr 0.8.2, protocol 20:** `agent.prompt` accepts only `target`, `text`, and optional wait settings. Subscription events expose pane output matches, agent status, and scroll changes. Pane/session metadata can report Pi session ids and paths, but there is no semantic assistant message, block, final response, or opaque per-prompt metadata channel.
- **Pi 0.84.4:** extensions can observe `input`, `message_start`/`message_update`/`message_end`, `turn_end`, `agent_end`, and `agent_settled`; sessions persist stable entry/message/tool-call ids in JSONL; JSON/RPC modes and typed terminating tools are available. Ordinary assistant output is still prose/tool content, so lifecycle hooks alone cannot truthfully invent heading/list semantics.
- **Firstmate:** currently routes normal text through Herdr and reads process/pane state. It exposes no Magistrate conversation API in this repository.

Therefore this repository implements the receiving side but does not pretend the missing producer exists. The smallest upstream integration is:

1. Extend the Herdr prompt seam (a protocol-versioned additive field) to carry an opaque host-owned context containing `turn_id` and `assistant_message_id` beside, not inside, prompt text. Those ids must not be model-selectable or inferred from matching prose.
2. Have Firstmate pass that context unchanged when routing the captain prompt and provide the Gateway event URL plus a command-scoped service credential out of band (never in the prompt, terminal, tool arguments, or session transcript).
3. Install a Pi extension/harness adapter that registers one closed structured-response tool. The host emits `assistant.started`; validated tool updates emit explicit block upserts/removes; successful tool completion emits the full `assistant.completed`; provider abort/error emits `cancelled`/`failed`.
4. Render a plain-text projection of the explicit tool document in the terminal so normal Herdr observability remains useful. Do not parse that terminal rendering back into JSON.
5. Add equivalent explicit adapters for non-Pi harnesses. Until an adapter is present, emit no semantic event and let the existing terminal fallback remain authoritative.

Pi session/message ids are useful producer evidence but are not substitutes for the Gateway ids: they do not currently carry a reliable mapping to a Magistrate captain turn.

## Test and rollout evidence

Focused automated coverage lives in:

- [`gateway/tests/test_structured_responses.py`](../gateway/tests/test_structured_responses.py): schema rejection, lifecycle ordering, stable ids, idempotency, correction, completion authority, terminal races, migration, HTTP auth/bounds, and WebSocket delivery;
- [`frontend/tests/magi-response.test.ts`](../frontend/tests/magi-response.test.ts): independent allowlist/bounds/URL checks, canonical reconciliation, revision updates, and malformed fallback;
- [`frontend/tests/chat-terminal.web.test.js`](../frontend/tests/chat-terminal.web.test.js): mixed text/structured history, five-heading phone overflow, repeated socket delivery, in-place revision, inert literal HTML, reserved actions, and unknown-block fallback in the real Expo web bundle.

See [`docs/evidence/magi-structured-response-v1/README.md`](evidence/magi-structured-response-v1/README.md) for the live run ledger and screenshots. The live Firstmate trial is intentionally split: the current upstream stack proves unchanged terminal fallback, while an authenticated explicit event producer proves the new canonical-to-native path. Claiming Firstmate-originated structure before the upstream context/tool adapter exists would be fabricated evidence.
