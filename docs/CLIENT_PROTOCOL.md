# Client protocol — canonical API/data contracts

Authority: [production matrix](PRODUCTION_STATUS.md). **Baseline contract:
COMPLETE.** This is not a claim that future tenant/project/billing APIs exist.
Actual request validators are exported without app startup or deployment DB access:

```sh
cd gateway
uv run python -m scripts.export_client_protocol > ../.release/client-protocol.json
```

The output is `magistrate.client-protocol.v1`: native request, Activity catch-up,
objective tool, discriminated execution events, verified completion evidence,
decision batches and decision-answer JSON Schemas from their Pydantic validators,
plus channel/path metadata. The CLI uses a disposable test database because the
legacy DB module initializes on import; it clears deployment configuration before
loading validators. It is not a hand-maintained copy or a full response OpenAPI schema. `test_client_protocol_export.py` guards determinism, closed
identity fields and executable routes. Keep output with the candidate hash; do
not commit production data. Full HTTP discovery remains Gateway's OpenAPI.

## Authentication and ownership

Use the short-lived bearer in `Authorization`, never a query string. Ownership
comes from the verified principal, not user/tenant IDs in request JSON. Native
provider refresh authority is a rotating SecureStore token; web refresh
continuity is the Secure HttpOnly SameSite cookie. Web JavaScript does not retain
refresh credentials. `401`, expiry, logout or principal change clears memory and
prior-principal caches before protected remount. Cosmetic components must not
make authorized requests: an incidental 401 invalidates the real session.

Apple/Google login challenge, exchange and refresh are `/api/v1/auth/provider/*`.
Provider availability is configuration, not a live sign-in. Integration OAuth
`/api/v1/auth/{provider}/connect` is separate from login identities. Permission
presentation/notification modes never grant execution authority.

## Native human conversation

| Operation | Contract |
|---|---|
| Submit | `POST /api/v1/magi/messages`; `client_message_id`, exact `content`, optional `conversation_id`, `source` text/voice, bounded stored attachments, explicit `retry_failed` |
| Current | `GET /api/v1/magi/conversations/current`; authoritative current thread for the principal |
| Identified read | `GET /api/v1/magi/conversations/{conversation_id}`; another owner's ID returns not found |
| Replay | `GET /api/v1/magi/conversations/{conversation_id}/replay?after=...`; canonical change sequence |
| Cancel | `POST /api/v1/magi/messages/{client_message_id}/cancel`; durable native cancellation, not a worker kill |
| Diagnostics | `GET /api/v1/magi/diagnostics`; bounded content-free counters |

Responses identify `magi.native-chat.v1`. Preserve canonical IDs, timestamps,
ordering and monotonic message revisions. Native statuses are pending, completed,
failed or cancelled. A duplicate `client_message_id` is owner-scoped; changed
facts under an already bound key conflict. An explicit retry revises the same
failed canonical pair. A response with failed status is not success merely
because HTTP returned 200. Assistant text is exact final provider bytes after
validation; partial/reasoning/tool envelopes are not fabricated prose.

`MagiConversation.ts` normalizes wire/revision data;
`MagiConversationSession.ts` is the one shared Chat/Voice reactive record. Persist
only principal-qualified canonical data and genuine pending sends. Successful
history is authoritative and prunes stale cache. Do not infer completion from
text, optimistic counts, worker output, prompt boundaries or a notification.

## Socket and replay

Connect WSS `/api/v1/events`; first frame is exactly:

```json
{"type":"auth","token":"SHORT_BEARER_FROM_AUTH","activity_after":0}
```

The token above is a notation, never a real credential. Do not put it in the
socket URL. Server acknowledgement is `connected`, schema
`magistrate.events.v2`. Conversation events have type `magi_messages` and payload
schema `magi.native-chat.v1`; structured Activity uses `activity_records`.
Monotonic revisions and durable HTTP/replay remain authority after reconnect;
duplicate frames do not append duplicate chat rows. No terminal/worker/pane or
legacy captain output is delivered as human conversation. Bounded first-frame
validation and scope checks remain mandatory.

## Execution, Attention and files

- `/api/v1/firstmate/execution-events` accepts strict
  `firstmate.execution-event.v1` with immutable event ID and objective/task/run
  causality. Completed events require `firstmate.completion-evidence.v1` with
  verified passed typed checks and allowlisted artifact references. Source
  authentication and durable evidence, not model prose, authorize a completion
  report. A completion report is a new assistant-only row in the original thread.
- `/api/v1/firstmate/decision-events` accepts a complete authenticated
  `firstmate.decision-events.v1` projection. Reads do not refresh Firstmate.
  Answering binds opaque ID/revision, owner/session, exact native user row and
  confirmation. Dismissing, viewing or acknowledging a notification is not an
  answer. Never add raw answer text to model-selectable arguments.
- `/api/v1/fleet` is the product projection; it omits task/run/pane/PID/terminal
  controls. Objective cancellation persists `requested`; only the authenticated
  structured cancellation event makes it observed. A request receipt is not a
  cancellation success claim.
- `/api/v1/activity`, `/snapshot`, `/replay`, `/catch-up` project durable events;
  Attention and notifications project the corresponding unresolved decisions.
- `/api/v1/uploads` issues opaque IDs and explicit `stored` state. A message
  attachment must match server metadata and owner. Download requires auth. A
  bare 200, filename, URL or local picker selection does not prove storage,
  extraction, model ingestion or attachment.
- Voice STT is `/api/v1/voice/transcribe`; its final text goes through the same
  native submit. Notification intents must pass `PendingIntentRouter` allowlists,
  wait for authenticated routing and match the intended principal/item.

## Compatibility and merge handoff

A1/A3/A4/A6/A10 must provide real tenant/project/GitHub/billing/context/storage
routes and schemas; do not invent client calls ahead of their merge. Preserve
closed server-derived ownership and add every new route to tenant-negative tests.
Additive optional fields must remain truthful; required semantic changes need a
new version and compatible rollout. A12's schema export derives validators but
cannot replace cross-language response tests or browser behavior.

Run `client-contract`, `integration`, `tenant-authorization`, `frontend` and
`spencer-hermetic`; then the real Spencer second-device/offline/deep-link tests.
See [SECURITY_MODEL.md](SECURITY_MODEL.md) for the trust boundary.
