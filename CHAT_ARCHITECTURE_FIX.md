# Chat architecture fix: the terminal is not the chat database

## Root cause

Captain chat derived its visible transcript from Herdr terminal output and then
persisted and re-parsed that output as if it were an immutable message log.

A Herdr snapshot is none of those things. It is a live rendering:

- a reply grows line by line while the agent writes it;
- it reflows when the pane width changes;
- its head scrolls out of the retained buffer;
- status overlays, composer frames, and harness panels land mid-frame, sometimes
  on top of a message row;
- Herdr exposes no durable message ids at all, so the gateway hashed row content
  to make one — and that hash changed every time the row changed.

Everything reported followed from that:

| Symptom | Why it happened |
| --- | --- |
| Duplicate user messages | The composer rendered its own message, then the same prompt was re-discovered as a terminal row with a different identity. |
| Duplicate assistant messages | Each re-read of a still-rendering reply hashed to a new id, so it appended instead of updating. |
| A reply re-appended after small text changes | Same cause: identity was content, and content mutated. |
| Tool calls, transport metadata, and terminal artifacts as prose | A snapshot carries no audience field, so classification was a client-side regex race against every harness's chrome. |
| Brittle reconciliation | `ChatIdentity`'s revision matching, optimistic counts, prompt-boundary tracking, and reload repair all existed to compensate for missing identity. |

Each previous fix (#61, #63, #67) narrowed the symptom. None could remove it,
because the source of truth was wrong.

## Design decision

The gateway is now the canonical source of truth for captain conversation, and
terminal parsing is demoted to an ingestion adapter.

- `gateway/app/conversation_store.py` owns conversations, turns, and messages.
- `POST /api/v1/captain/prompt` creates **exactly one turn and one canonical user
  message**, keyed by the frontend's existing `message_id`. Replaying the same
  `message_id` reuses both.
- Terminal snapshots are folded in by `ingest_terminal_rows`, which **upserts**
  the turn's primary reply and tool events into fixed slots. Evolving output
  revises a row; it can never append a second one.
- Rows that belong to no submitted turn are not recorded. An unattributed
  terminal row has no audience, so it fails closed rather than becoming chat.
- Every message is typed: `conversation` (visible), `tool` (bounded label,
  revealed only by the explicit "show tool calls" option), `internal` and
  `status` (never delivered to a chat client at all).
- The `captain` alias resolves only to an agent in Herdr's explicitly labelled
  `firstmate`/`captain` workspace (or an explicitly named legacy pane). A known
  harness is not a routing role: if that workspace is absent, routing fails
  closed instead of injecting the captain's prompt into an arbitrary worker.

### Schema

```
conversations         (id, user_id, target, created_at, updated_at)
conversation_turns    (id, conversation_id, client_message_id, prompt_key,
                       status, sequence_index, created_at, updated_at)
conversation_messages (id, turn_id, conversation_id, role, type, slot, text,
                       visible_in_chat, sequence_index, revision, source,
                       attachments_json, created_at, updated_at)
```

All three `created_at`/`updated_at` pairs are Unix epoch **milliseconds**. The
Gateway authors them; the composer's `Date.now()` is only an optimistic
placeholder until the canonical user row arrives. This avoids reconciliation
between client-millisecond and server-second values and makes reloads reproduce
the exact canonical time.

Cancellation is durable turn state, not a synthetic chat message. Every
delivered message carries `turn_status`; after a stop, the canonical user row
therefore returns with `turn_status: "cancelled"` on every reload and the client
renders `Response stopped` from that field. No separate `type: "status"` row is
delivered, because status rows are intentionally outside the chat payload.

Two constraints carry the whole guarantee:

- `UNIQUE(conversation_id, client_message_id)` — one turn per submission.
- `UNIQUE(turn_id, slot)` — one row per role in a turn. `slot` is `prompt`,
  `primary`, `tool:<n>`, or `internal:<n>`, and `sequence_index` is derived from
  it (`turn_index * 1000 + slot_offset`), so render order is fixed and stable no
  matter when a row is discovered.

## New flow

```
composer ──POST /captain/prompt {message_id}──► record_prompt()   → turn + user message
                                             └► herdr prompt      → provider
                                             └► record_primary_reply() if the
                                                harness answered synchronously
                                          ◄── {conversation: {turn_id, messages}}

poll  ──GET /conversations/captain/messages──► read_typed_rows() → parse → classify
socket ──WS /events (conversation_messages)──► ingest_terminal_rows()  → upsert
                                          ◄── canonical messages (WS sends only
                                              records whose revision changed)
```

The client contract is now just two rules: **append when a new canonical message
arrives, update when an existing one changes.** `frontend/src/services/CanonicalConversation.ts`
is the only place that implements it, and it does so by id, monotonic revision,
and sequence index — no text matching, timestamp comparison, optimistic
counting, prompt-boundary inference, or replay reconciliation. A delayed poll or
socket revision cannot roll a newer rendered revision backwards.

Opening captain chat restores the last strictly validated canonical snapshot
and genuine local `sending`/`failed` rows immediately, then reads Gateway. A
successful full list remains authoritative: it prunes/replaces cache-only rows
while monotonic revision checks keep a delayed lower revision from rolling a
newer row backwards. A transient network failure leaves the saved snapshot
visible under an explicit stale/reconnecting state; the next successful poll
replaces it. The cache never creates a turn, never accepts terminal-era rows,
and is not an authority. The history exposes `aria-busy=true` until initial
recovery settles, giving scroll anchoring and browser checks a deterministic
boundary. An upward reader scroll cancels every queued latest-position
operation, including a pending measurement retry, so asynchronous rendering
cannot pull the captain back down. The prompt endpoint writes its turn and user
row before awaiting Herdr, so an accepted prompt is already readable even while
assistant ingestion is pending.

Voice Mode shares the same record: `POST /api/v1/voice/moves` records a completed
move as a canonical turn, so chat and voice are one transcript rather than two
locally minted ones.

Attachments obey the same authority boundary. The user row stores only bounded,
validated metadata (`upload_id`, stable id, safe name, MIME type, size, and the
authenticated `/api/v1/uploads/{id}` reference) in `attachments_json`. Bytes stay
in the private upload store introduced by PR #55; no blob, filesystem path, or
client-claimed metadata enters the transcript. A healthy reload therefore
renders the same attachment from Gateway; the validated cache is only a
transient reconnect display.

## What was removed or simplified

- Captain chat no longer calls `/api/v1/agents/{id}/history` at all.
- `ChatCanvas` lost `reconcileCaptainHistory` (~90 lines of segment matching,
  positional turn pairing, and reload repair) and the whole captain branch of
  `appendHistoryMessages` (identity seeding, optimistic counts, observed-prompt
  boundaries, tool accumulation).
- `ConversationSession` lost `insertConversationMessageAfter`, which existed only
  to splice a discovered reply under the right user row.
- Captain persistence is now two explicitly non-authoritative maps:
  `magistrate.chat.canonical.v1.captain` is keyed by canonical message id, and
  `magistrate.chat.pending.v1.captain` is keyed by `client_message_id` and holds
  only genuinely unacknowledged sends. The canonical map is restored only after
  strict identity/revision/sequence validation and is replaced by the next
  successful full list. The old v1/v2 captain arrays are **deleted, not
  migrated** because they have no trustworthy canonical identity. Worker
  targets retain their transitional `magistrate.chat.messages.v2.<target>`
  cache.

## Production migration

The new tables and the `prompt_key` column are created by `init_db()` at startup
with `CREATE TABLE IF NOT EXISTS` and a guarded `ALTER TABLE`. An existing
deployment database (the demo runs against a SQLite file outside the checkout)
gains those objects on restart; no pre-existing application table or row is
rewritten or dropped. The canonical record starts empty and fills from the next
prompt onward. An idempotent, canonical-table-only normalization multiplies
unmistakable preview-era epoch-second timestamps by 1000, so a database briefly
run from an earlier revision of this branch remains readable at millisecond
precision. The guarded `attachments_json` column addition defaults existing
canonical rows to `[]`; upload bytes remain in their existing store. There is no
downgrade step.

## Remaining limitations

- **Worker panes are still terminal-derived.** They contain autonomous and
  Firstmate-authored turns with no Magistrate client submission id, so a record
  of only prompts sent through the app would be incomplete. `/agents/{id}/history`,
  `sanitizeTerminalHistory`, and `ChatIdentity`'s revision matching therefore
  remain in use for those targets only. They are marked `TRANSITIONAL` in
  `app/(tabs)/chat.tsx`; durable identity for every worker-side turn is what
  would retire that code.
- **Initial reply attribution still matches on prompt text.** The adapter pairs a
  snapshot segment with a turn by comparing the submitted prompt (whitespace
  collapsed; `prompt_key` holds the exact text the provider received, including
  any attachment manifest). Matching runs newest-first, so a repeated phrase
  cannot steal a newer turn's reply. After that first attribution, a long Claude
  reply may push its prompt off the alternate-screen viewport; a leading segment
  can continue only when at least 40 characters of assistant prose overlap the
  primary reply of exactly one recent turn. No overlap or ambiguous overlap fails
  closed. A prompt whose terminal rendering differs by more than whitespace, or
  one that scrolls away before any prose is attributed, still leaves the turn
  without a reply. Do not "fix" this by attributing unmatched trailing prose to
  whichever turn is open: the captain pane has other audiences, and a reply under
  the wrong prompt is worse than a missing one.
- **`ingest_error` travels with the record.** When the live snapshot cannot be
  read, `GET /conversations/{target}/messages` still returns the canonical
  transcript and reports the failure in `ingest_error` rather than presenting a
  stale record as current.
- **A turn is `streaming` while the pane is working.** Herdr cannot tell us
  that a harness has finished writing from prose alone. The adapter moves the
  turn to `answered` only on an observed idle/done state, an explicit
  synchronous provider return, or a later user boundary. This keeps reloads and
  socket/poll delivery honest about an unchanged but still-live reply.
- **Tool events are labels, not transcripts.** Only the bounded
  `tool_call_preview` label is recorded, because a tool row's raw text is a shell
  command or file excerpt that can carry tokens and paths. The full activity
  stays in the terminal, reachable via `/api/v1/captain/output`.
- **One primary reply per turn is a loss-resistant join.** A harness interleaves
  prose with tool activity, so a turn's prose blocks are folded into one message
  and render as one assistant bubble. Exact containment, reflow-normalized
  containment, and conservative suffix/prefix overlap merge sliding terminal
  windows without duplicate text; an unsafe disjoint window fails closed rather
  than replacing the captured prefix. There is no second arbitrary text cap in
  the gateway; the only remaining bound is Herdr's own retained scrollback.
  Before this change only the *first* prose block was shown; the rest was
  silently dropped.
- **The delivered window is bounded to 200 messages.** A full list read is
  authoritative, so a client viewing a very long conversation keeps only the most
  recent 200 canonical records. Older turns remain in the database; paging them
  back is not implemented. Canonical ingestion always asks Herdr for its maximum
  retained line range, rather than applying the worker-pane 400-line default a
  second time.
- **Tool chips are standalone rows again.** #67 attached tool previews under the
  reply; canonical tool events are their own records and render in sequence
  between the prompt and the reply, matching how worker threads already show
  them. `magistrate.chat.show-tool-calls` still gates them.

- **The terminal is still an ingestion adapter, not a durable upstream transcript.**
  Herdr currently exposes mutable ANSI scrollback and pane status, not stable
  message/session ids for the primary process. The gateway persists every
  attributed reply before later reads, folds overlapping sliding windows
  monotonically, and fails closed on an unsafe disjoint read. A prompt that
  scrolls away before any attributable prose, or a window with no unique overlap,
  can still remain missing; choosing the latest open turn would be worse because
  the pane has other audiences.

## Verifying in the deployed app

1. Restart the gateway so `init_db()` creates the new tables, then confirm:
   `sqlite3 "$MAGISTRATE_DB_PATH" '.tables'` lists `conversations`,
   `conversation_turns`, `conversation_messages`.
2. Open Chat. It starts empty on the first load after the fix — terminal-era
   local arrays are discarded and the canonical record has no turns yet.
3. Send one message. Expect exactly one captain bubble and, when the agent
   answers, exactly one assistant bubble. Watch it for a minute while the agent
   writes: the reply text should change in place and never duplicate.
4. Reload the page. The transcript (including attachment name, type, size, and
   attached state) must be identical — it is re-read from the gateway, not
   replayed from the terminal or a local attachment cache.
5. Send the same wording twice. Two captain rows, each with its own reply.
6. Send a message and press Stop (or Escape). The row shows "Response stopped",
   and output the agent produces afterwards must never appear as that turn's
   reply — the turn is cancelled server-side and frozen.
7. Turn on Settings → show tool calls. Only bounded labels ("Running…", "Bash",
   "Read") appear. Nothing containing `jsonrpc`, `pane_id`, `FIRSTMATE_OP`,
   `/calm`, a shell command, or a file excerpt may appear anywhere in chat.
8. Confirm the record directly:
   `curl -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/v1/conversations/captain/messages"`
   — every message has an `id`, a `turn_id`, a `type`, and a `sequence_index`,
   and no `internal` or `status` record is present.
