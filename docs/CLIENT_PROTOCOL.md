# Client protocol — canonical API/data contracts

Authority: [production matrix](PRODUCTION_STATUS.md). Merged repository contract:
**COMPLETE**. External service/device acceptance remains separate.

```sh
mkdir -p .release
(cd gateway && uv run python -m scripts.export_client_protocol) > .release/client-protocol.json
```

`magistrate.client-protocol.v1` exports the **actual** Pydantic validators for
native messages, Activity catch-up, objective tools, discriminated execution
facts, completion evidence, measured usage, decision batches/answers, billing
Checkout/Portal, memory writes, perception/confirmation and execution requirements.
It also exports executable domain/channel paths. The CLI clears deployment
configuration before imports and uses disposable state; it never starts the app,
opens a deployment DB, requests a provider or invokes a worker. The deterministic
export tests include poisoned PostgreSQL/state configuration and route parity.
This is a schema bundle, not a full response OpenAPI specification.

## Identity and onboarding

Use short-lived `Authorization: Bearer` credentials, never query tokens. Owner
and personal-workspace/tenant qualifiers come from the server principal, never
client JSON. Missing/foreign opaque project/repository/message IDs return the
same not-found response. Principal-scoped caches clear on logout/expiry/401/change.
Cosmetic surfaces must not issue authorized requests just to decorate the shell.

Apple/Google `/api/v1/auth/provider/*` handles challenge/exchange/refresh.
Native refresh authority is rotating SecureStore data; web refresh is only a
Secure HttpOnly SameSite cookie. Login identities and integration OAuth have
separate account kinds; matching email never auto-links subjects.
`/api/v1/account/onboarding` resumes welcome, name, GitHub OAuth identity and
signed subscription state. The Free credit projection or creating Checkout does
**not** complete paid onboarding. Account erasure requires exact authenticated
confirmation at `DELETE /api/v1/account` and retires owned credentials/content.

## Human conversation

| Operation | Contract |
|---|---|
| Submit | `POST /api/v1/magi/messages`: owner-qualified `client_message_id`, exact content, optional conversation, text/voice source, stored attachments, explicit retry and request-bound `explicit_confirmation` |
| Current | `GET /api/v1/magi/conversations/current` |
| Identified read | `GET /api/v1/magi/conversations/{conversation_id}` |
| Replay | `GET /api/v1/magi/conversations/{conversation_id}/replay?after=...` |
| Cancel | `POST /api/v1/magi/messages/{client_message_id}/cancel`; not a worker kill |
| Diagnostics / route costs | `GET /api/v1/magi/diagnostics`, `/api/v1/magi/model-routes`; content-free |

Responses are `magi.native-chat.v1`. Canonical IDs/timestamps/order and monotonic
revisions remain authoritative. Pending/completed/failed/cancelled are distinct;
a failed row with HTTP 200 is not success. Reusing a bound key with changed facts
conflicts; retry revises the same failed canonical pair. Final provider bytes,
not reasoning/tool envelopes or inferred stdout, become assistant conversation.

`MagiConversationSession.ts` is the shared reactive Chat/Voice record. Persist
only principal-qualified canonical rows and genuine pending sends; authoritative
history prunes stale cache. There are no worker targets or transport flags.

## Socket and replay

WSS `/api/v1/events` authenticates with a bounded first frame:

```json
{"type":"auth","token":"SHORT_BEARER_FROM_AUTH","activity_after":0}
```

This is notation, not a credential. Acknowledgement is `connected`, schema
`magistrate.events.v2`. Native frames are `magi_messages`, Activity frames
`activity_records`. HTTP replay and revisions survive reconnect; duplicates never
append duplicate canonical rows. No worker/terminal/Pi stream is conversation.

## Merged domains

- `/api/v1/projects` and owner-qualified project/repository routes persist the
  personal-workspace hierarchy. Repository metadata is not GitHub authorization.
- `/api/v1/github/app/*`, `/installations/{id}/reconcile`, and
  `/api/v1/github/repositories/{id}/...` use active owner-bound GitHub App access.
  OAuth account identity alone cannot authorize repository content. Tokens stay
  server-only; callbacks and signed webhooks are distinct ingress seams.
- `/api/v1/billing/catalog`, `/account`, `/checkout`, `/portal` accept catalog IDs,
  not caller-selected customer/balance authority. Only raw-byte signed
  `/api/v1/billing/webhooks/stripe` events change paid state. The legacy single-price
  webhook/config remains migration compatibility, not new activation guidance.
- `/api/v1/magi/memory/*` takes an owner-resolved project plus optional repository.
  Writes/tombstones require command; search/context/audit require read. Context
  carries bounded facts/provenance, never permission or executable instructions.
- `/api/v1/perception/events` and its confirmation route normalize consented
  device observations. Even confirmed results have `executes_action: false`.
  Low-confidence/high-impact/neural/subvocal drafts require exact confirmation;
  another existing product API must independently authorize any action.
- `/api/v1/uploads` returns explicit server `stored`/attached state and opaque IDs.
  Authenticated signed access, deletion, digest/owner checks and private storage
  are in `uploads.py`. No path or local-picker selection proves processing.
  Attachment-bearing Magi turns have no execution tools.
- `/api/v1/voice/transcribe` returns STT text for the **same** Native Magi thread.
  Notifications use typed allowlisted intents and authenticated routing, not
  command authority. Status exposes owner-only receipt counts; ticket acceptance,
  provider receipt, physical arrival and viewing are separate facts.

## Execution and Attention

Local authenticated `/api/v1/firstmate/execution-events` and decision projections
are disabled as producer ingress in hosted mode. Hosted callbacks under
`/api/v1/hosted-execution/objectives/{objective_id}/...` instead require the exact
objective-bound worker bearer. Both feed the same closed structured ledgers.

`firstmate.execution-event.v1` preserves immutable event/objective/task/run
causality. Production terminal facts need measured usage; completed facts also
need verified `firstmate.completion-evidence.v1`. Model prose or a process exit
cannot manufacture completion. Fleet omits pane/PID/raw runtime controls;
cancellation stays requested until observed. Activity snapshot/replay/catch-up
reads persisted facts, never starts a worker.

Decision batches are complete `firstmate.decision-events.v1` projections.
`firstmate.answer_decision` arguments contain only opaque ID/revision; the host
loads exact answer bytes from the owner's canonical user row with bound
confirmation. Viewing/dismissing/acknowledging a notification never answers it.

Preserve additive compatibility, closed ownership and canonical identities.
Required semantic changes need versioning and Gateway-before-client rollout.
Run `client-contract`, `tenant-authorization`, `integration`, `frontend` and
`spencer-hermetic`; physical/offline/second-device checkpoints remain external.
See [SECURITY_MODEL.md](SECURITY_MODEL.md).
