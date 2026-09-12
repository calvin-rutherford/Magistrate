# Magi native chat — Phase 1 (Steps 0–7)

## Scope and verdict boundary

Phase 1 makes ordinary Magi Chat and Voice Mode use one direct, non-streamed
provider completion behind authenticated FastAPI. Gateway reserves and owns the
canonical user/assistant pair in additive SQLite tables. This slice deliberately
does **not** add tools, agent/harness routing, model selection, tool approval,
streaming, background work, Herdr operation, or deployment. Browser and
simulator-compatible tests are evidence; no physical-iPhone acceptance is
claimed.

## Step 0 baseline

Baseline commit: `ea1a1aaacb473ee6c15aae8964537411479bce42`.

At that revision:

- the iPhone/web composer posted to `/api/v1/captain/prompt`;
- captain persistence lived in the existing `conversation_turns` and
  `conversation_messages` ledger, with Pi semantic ownership selected by
  default and terminal history retained as the unowned fallback;
- Voice Mode posted to `/api/v1/voice/moves`, which delegated through
  Firstmate/captain routing;
- captain WebSocket updates replayed `conversation.v1` rows;
- there was no `/api/v1/magi` router, provider-independent native model
  interface, native pair schema, or native reliability runner.

Those paths remain in the tree as explicit rollback compatibility. They are no
longer the production-default normal-chat transport.

## Normal request path

```text
authenticated Chat / Voice UI
  -> POST /api/v1/magi/messages
  -> principal from bearer session (never from request JSON)
  -> BEGIN IMMEDIATE + owner/client-id uniqueness claim
  -> reserve one user row and one empty pending assistant row
  -> bounded completed native history + bounded project/account context
  -> one async, non-streamed OpenAI-compatible completion
  -> validate complete final text and reject tool/incomplete envelopes
  -> atomically update the reserved assistant row
  -> return/replay the same canonical ids and exact provider text
```

`gateway/app/magi_chat_service.py` has no imports from Herdr, Firstmate, Pi,
harness routing, terminal parsing, or execution tools. The adapter contract in
`magi_model.py` is provider-independent; Phase 1 supplies exactly one concrete
OpenAI Chat Completions adapter. Provider credentials stay in Gateway's
server-only environment and are never returned or logged.

The adapter requests `stream: false`. Only `finish_reason: stop` with non-empty,
strict UTF-8 text is accepted. `length`, tool calls, malformed envelopes,
oversize responses, unsafe control characters, timeouts, and provider errors become persisted failures;
partial provider prose never masquerades as a completed answer. Completed text
is stored without `strip`, Unicode normalization, newline conversion, Markdown
reconstruction, chunk assembly, or terminal parsing. Limits are 200,000 Unicode
characters and 256 KiB UTF-8.

## API

All routes require an unexpired bearer session and enforce existing scopes.
Ownership misses return `404`, including a syntactically valid conversation id
owned by another principal.

| Route | Scope | Purpose |
| --- | --- | --- |
| `POST /api/v1/magi/messages` | `command` or `voice` | Submit/replay one client id; set `retry_failed: true` only to retry a failed pair. |
| `GET /api/v1/magi/conversations/current` | `read` | Read the principal's default native thread. |
| `GET /api/v1/magi/conversations/{id}` | `read` | Read one explicitly owned native thread. |
| `GET /api/v1/magi/conversations/{id}/replay?after=` | `read` | Replay append/update changes by monotonic cursor. |
| `POST /api/v1/magi/messages/{client_id}/cancel` | `command` or `voice` | Cancel a still-pending reserved assistant row. |
| `GET /api/v1/magi/diagnostics` | `read` | Read content-free, principal-scoped counters and aggregates. |

The message body accepts only `client_message_id`, `content`, optional
`conversation_id`, `source: text|voice`, authenticated upload references, and
`retry_failed`. Extra keys—including owner/user/principal identity—are rejected.
Text is 1–100,000 characters. At most ten already-stored, owner-scoped uploads
may be attached; only bounded authenticated references enter chat persistence
or model context, not file bytes.

After WebSocket authentication, `chat_mode: native` selects native SQLite
poll/replay and emits `magi_messages`. It does not read terminal snapshots or the
legacy captain ledger. HTTP history is authoritative on startup/reconnect.

## Persistence, ordering, and recovery

`gateway/app/db.py` only adds:

- `magi_conversations` — principal-owned threads and one default thread per
  principal;
- `magi_messages` — one immutable-role row per reserved identity, status,
  revision, sequence, reply edge, attachment references, and final visible text;
- `magi_message_changes` — append/update replay cursor;
- `magi_chat_diagnostics` — content-free counters/latency/size aggregates.

No old table is renamed, rewritten, or dropped. Foreign keys bind every message
to its conversation **and owner**. Unique constraints cover
`(owner_user_id, client_message_id)` for user submissions and
`(conversation_id, sequence_index)`. `BEGIN IMMEDIATE` makes the claim atomic
across threads/process connections: exactly one concurrent caller starts the
provider; all others replay the existing pair. Identical prompt text with
different client ids remains distinct.

The provider task is strongly referenced and shielded from an originating HTTP
request cancellation, so an app background/reconnect can observe its eventual
persisted state. Explicit cancel wins atomically and late provider output cannot
overwrite it. A process itself cannot retain a network completion through a
restart; startup changes any orphaned pending assistant to failed
`server_restart`, preserves both ids/content attribution, appends a revision,
and permits an explicit same-id retry. Failed retries reuse the canonical pair,
increment attempt state, and issue a deterministic provider idempotency key.

Provider context includes at most forty prior completed native rows and at most
100,000 characters, admitting whole historical messages from newest to oldest.
The current prompt is always complete. No terminal, execution, hidden tool, or
legacy transcript row can enter that context.

## Client integration

`frontend/src/api/client.ts` defaults
`EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED=true` and
`EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED=false`. Chat and Voice Mode both call
`sendMagiChatPrompt`; native mode ignores the retained harness/model profile UI
and does not invoke stop/interrupt against an agent. The shared canonical
normalizer marks assistant content as `magi-native`, reconciles by server id and
revision, preserves empty pending/failed states truthfully, and feeds the same
reactive captain thread/cache used by both surfaces. Reload and reconnect read
native history. Voice speaks the complete persisted assistant message and uses
Magi—not Firstmate—copy.

The client bundle contains only transport booleans and Gateway URL. It never
contains the provider key.

## Flags and rollback

Gateway defaults:

```dotenv
MAGISTRATE_NATIVE_CHAT_ENABLED=true
MAGISTRATE_LEGACY_CHAT_ENABLED=false
```

Expo defaults:

```dotenv
EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED=true
EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED=false
```

The deployment guard requires exactly one server transport to be enabled and
passes those exact values into the Expo export. Native activation also requires
`OPENAI_API_KEY`. For a bounded rollback, stop submissions, set native false and
legacy true, rebuild/restart, and enable/configure Pi ownership only if that
legacy route is desired. Do not delete native rows or restore a database merely
to switch transports. Existing legacy rows remain readable after rollback;
native rows remain intact for a later re-enable.

## Backup and additive migration proof

Before an enabled native deployment, `scripts/deploy_magistrate.sh` refuses a
missing, symlinked, checkout-local, wrong-owner, or wrong-mode database. It uses
SQLite's online backup API into a service-owned mode-`0700` directory, validates
`PRAGMA integrity_check`, compares every source/backup table row count, sets the
artifact mode to `0600`, and writes exact-commit and SHA-256 sidecars before any
build or restart. `scripts/test_deploy_magistrate.sh` restores/opens that
artifact and reads a pre-existing legacy conversation row. The gateway migration
test starts from an old schema snapshot, runs current `init_db()`, verifies every
legacy value byte-for-byte, and exercises a new native turn afterward.

Emergency restore remains whole-database restore: stop Gateway, preserve the
failed database, verify the selected backup/hash/commit, copy it to the same
absolute path with original ownership and mode, start Gateway in legacy mode,
and run authenticated smoke checks. Never selectively delete migration rows.

## Deterministic reliability gate

Hermetic command (no provider credentials or conversation content):

```sh
cd gateway
PYTHONPATH=. uv run python -m scripts.magi_native_reliability
```

The gate uses a disposable SQLite file and deterministic async fake provider.
It performs 120 canonical submissions, including a 16-way same-id reconnect
storm, rapid concurrent sends, repeated identical text with distinct ids,
Unicode, Markdown/code, thirty numbered items, an 87k-character response,
forced provider failure plus explicit retry, fresh-service restoration, and a
foreign-principal read. Every result is compared to persisted UTF-8 bytes and
causal ids. The same gate is part of pytest.

Observed local run on 2026-09-12 (aggregate output only):

```json
{"complete":120,"cross_attribution":0,"duplicate_submissions_observed":17,"duplication":0,"failed_attempts_recovered":1,"legacy_chat_reads":0,"maximum_completion_latency_ms":53,"maximum_response_bytes":87853,"maximum_response_characters":87797,"pi_ownership_chat_reads":0,"provider_calls":121,"raw_internals":0,"result":"PASS","retries":1,"schema_version":"magi.native-chat-reliability.v1","submissions":120,"terminal_chat_reads":0,"tool_calls":0,"truncation":0,"wall_clock_ms":3880}
```

Acceptance is exactly 120/120 complete with zero truncation, duplication,
cross-attribution, raw execution internals, legacy reads, terminal reads, Pi
ownership reads, and tool calls. Provider calls are 120 successful turns plus
one deliberately failed attempt; duplicate/reconnect calls add none.

Repository gates on the same final tree also completed: Gateway `453 passed, 1 skipped`;
Frontend `212 passed, 0 failed`; TypeScript passed; Expo lint exited zero
(`0 errors`, 29 warnings); static web export completed; and both guarded
deployment contract scripts passed.

The real-provider runner is opt-in and defaults to 100 submissions:

```sh
cd gateway
MAGISTRATE_RUN_REAL_MAGI_RELIABILITY=1 OPENAI_API_KEY=... \
  PYTHONPATH=. uv run python -m scripts.magi_real_provider_reliability
```

It uses a disposable database and prints only aggregate counts, sizes, and
latency. It refuses to run without both opt-in and credential. It was **not run
for this implementation**, so this document makes no real-provider or physical
device acceptance claim.

## Diagnostics and exclusions

`GET /api/v1/magi/diagnostics` reports submitted/completed/failed/duplicate/
retry/tool-call counts, response character aggregates, completion latency, and
migration-only legacy/terminal/Pi read counters. It contains no prompts,
responses, attachment names, provider payloads, owner ids, credentials, or
terminal data. `/api/v1/diagnostics/soak` includes that owner-scoped aggregate
plus active flag state.

Phase 1 intentionally leaves these for later phases: streaming, tools and
approval UI, agent/harness/model routing, background work, multi-model policy,
and physical-iPhone release acceptance.
