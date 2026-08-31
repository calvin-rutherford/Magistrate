# Truthful provider, telemetry, and attachment states

The product principle is **real data or an honest unavailable/error state, never
a demo fake**. This document records the rules that make a fabricated state
unreachable, and which providers are deliberately deferred.

## What "connected" requires

`GET /api/v1/auth/providers` never reports `connected` unless **all three** hold
(see `_provider_connection_state` in `gateway/app/main.py`):

1. **Operator configuration exists** — the adapter's `is_configured()` is true,
   which for GitHub means `GITHUB_OAUTH_CLIENT_ID`,
   `GITHUB_OAUTH_CLIENT_SECRET`, and `MAGISTRATE_OAUTH_CALLBACK_BASE_URL` are
   all present.
2. **A credential is stored** — `connected_accounts` joins a real
   `oauth_credentials` row. A `status = 'connected'` row on its own is not
   evidence of anything.
3. **That credential has not expired** — `oauth_credentials.expires_at` is in
   the future.

Any missing piece downgrades to a specific state, never to a connected one:

| Situation | Reported status | Identity shown |
|---|---|---|
| No OAuth configuration on the gateway | `unavailable` | none |
| Provider deferred for this release | `unavailable` (`deferred: true`) | none |
| No stored credential | `disconnected` | none |
| Stored credential expired | `expired` | username |
| Configured, stored, unexpired | `connected` | username |

Every non-connected row carries a safe `unavailable_reason`. Configuration
**names** may appear in that reason; configuration **values** never do.

Disconnecting deletes the credential row (`db.disconnect_account`), so a later
listing cannot reconstruct a connected-looking state from a stale row.

The client repeats the same rule in `normalizeAuthProvider`
(`frontend/src/api/client.ts`): an unknown status string, or a `connected`
status on a provider the gateway marked unavailable, fails closed. A stale or
tampered payload therefore still cannot render a fake connection.

## Deferred providers: Jira and Microsoft Teams

**Jira and Teams are deferred for owner alpha and are visibly unavailable.**
They have no OAuth application, redirect URI, tenant consent, scopes, or test
account, so `is_configured()` returns `False` and `is_deferred()` returns `True`
unconditionally. Their connect action is disabled and labelled `DEFERRED`, and
`/api/v1/jira/issues` and `/api/v1/teams/mentions` contribute no records rather
than sample data.

They become available only when real credentials exist. To enable one, supply
its OAuth application, redirect URI, tenant consent, and scopes, then remove the
hardcoded `is_configured`/`is_deferred` overrides in
`gateway/app/providers/jira.py` / `teams.py`. Until then, **do not create a demo
`connected` state** for either provider — that is the exact failure this
contract exists to prevent. GitHub is the required provider bridge for owner
alpha.

## Telemetry: no invented metrics

`HerdrClient.get_snapshot` previously returned a placeholder `version` when
neither the socket nor the CLI answered, which made `herdr_socket_connected`
read as **true** while Herdr was unreachable. The empty snapshot now carries no
version, so:

- `/api/v1/health` reports `status: 'degraded'` with a `degraded_sources` list
  whenever Herdr or Firstmate was not observed, and `herdr_version: null`.
- `/api/v1/runtime` reports `version: null` and `protocol: null` when
  disconnected instead of substituting values.
- Counts derived from a failed source render as `—`, not as `0`.

## Attachments: real processing state

`save_upload` returns a server-issued `status: 'stored'`, which claims only that
the bytes were persisted and the declared type was validated against content —
no transcoding, extraction, or provider ingestion. `POST /api/v1/uploads` adds
`attached`, reflecting whether the gateway actually associated the upload with a
chat message.

The client refuses to treat a 200 as success on its own: `uploadChatFile` throws
unless the record carries `status: 'stored'`, and `sendCaptainPrompt` refuses to
reference an unconfirmed upload. In chat, an attachment reads `Uploading…` until
the gateway confirms storage, `Stored, not yet sent` once it has, `Attached`
only after the prompt carrying its manifest was accepted, and `Upload failed`
otherwise.

## Server-issued identifiers

Where the server knows an entity, its id is the server's:

- Gateway history rows keep the gateway's `message.id`; a row without one is
  dropped rather than given an invented local id.
- A chat response with a `runId` is keyed on it.
- A completed voice turn is keyed on the gateway's `move_id`.

Client-minted ids remain only for entities the client originates before any
server round trip (a locally composed prompt, which doubles as its idempotency
key).
