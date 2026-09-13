# Friend Beta security boundary

Magistrate's active beta architecture is Expo/React Native → FastAPI Gateway →
provider-native Magi chat, with Herdr/Firstmate retained for explicit fleet and
legacy surfaces. The legacy Django subsystem is not part of this request path.
The beta remains one operator-owned deployment and runtime; multiple invited
principals do **not** make it tenant-isolated SaaS.

## Identity and credentials

The server operator configures `MAGISTRATE_BOOTSTRAP_SECRET`,
`MAGISTRATE_BOOTSTRAP_USER_ID`, `MAGISTRATE_SESSION_SCOPES`, and an explicit
`MAGISTRATE_CORS_ORIGINS`. The owner bootstrap credential issues only the owner
principal and never enters a frontend bundle.

When `MAGISTRATE_FRIEND_BETA_ENABLED=true`, the operator may provision a unique
`mgb_…` access grant for each friend/device with
`gateway/scripts/friend_beta_access.py`. Assignment per device is an operator
policy, not device attestation: the code remains a bearer credential and a
later redeemer replaces the earlier session. A principal may have only one
active grant; reissue requires revocation or expiry. The database stores only
its SHA-256
digest, principal, scopes, expiry, and redemption/revocation timestamps. Codes
are written once to a new mode-`0600` file for out-of-band delivery. Redeeming a
grant replaces its prior short-lived bearer. Friend Beta logout or operator
revocation revokes the grant, all derived sessions, and that principal's push
registration. Turning the feature flag off also closes existing friend bearers
and excludes friend principals from background delivery immediately; revoke
grants as the durable retirement step before or after that kill switch.

Native stores the access grant and current bearer only through
`GatewaySessionStorage`/Expo SecureStore using device-only Keychain
accessibility. The browser stores only its short bearer and requires code
re-entry after expiry. The old native AsyncStorage bearer is deleted, never
migrated. Neither storage mechanism is proof of a physical-device result, and
an iOS uninstall is not evidence that Keychain material disappeared. Lost,
replaced, or re-enrolled devices require operator revocation before a new grant.

The safe invite scopes are `read,account,notifications`. `read` observes the
single shared deployment; it is not tenant-isolated fleet data. `command` and
`voice` can reach the shared operator runtime and the CLI refuses them without
`--allow-shared-runtime-access`. That acknowledgement does not reduce their
authority. The `response` producer scope is never issuable to a friend. Until a
merge-authority decision accepts shared-runtime access or a dedicated
least-privilege chat scope exists, friends remain observers and normal chat
submission is intentionally unauthorized.

## Request boundary

Every protected Gateway route enforces scope and derives ownership from the
bearer principal, never from a caller-supplied `user_id`. HTTP uses an Authorization
header. The events socket accepts the bearer in its first application frame,
never a query string. Friend profile onboarding must complete before protected
client routes mount; a prior principal's cached personalization and canonical
chat/activity state are cleared before another principal is shown.

Provider cards remain unavailable until real OAuth client configuration and
principal-owned account data exist. OAuth connect requires an exact allowlisted
app redirect; state is server-side, expiring, principal-bound, and single-use.
Do not add sample identities, provider tokens, access codes, runner addresses,
or Tailscale credentials to the repository or client.

## Deployment boundary

Production requires HTTPS/WSS-facing Gateway configuration, explicit CORS,
server-only provider credentials, a generated `MAGISTRATE_SECRET_KEY`, and an
absolute persistent `MAGISTRATE_DB_PATH` outside the release checkout. The
deploy guard rejects Friend Beta with legacy captain chat. Keep SQLite backups
with service state, not in Git or the frontend.

The private runner remains a deployment concern. Before claiming multi-user
production, add an identity provider/invite administration service, recovery
and device management, per-user authorization for every fleet/runtime source,
and tenant-isolation tests. The current access-grant CLI is restricted-beta
provisioning, not that production identity system. It also provides no
self-service recovery, device inventory, data-erasure workflow, or
application-level request throttling. Revocation ends access but does not erase
principal-owned rows; reusing that principal restores its retained data. Keep
the cohort bounded, apply redacted rate limits/monitoring at the HTTPS edge,
and revoke on abuse or loss.

The ordered operating and release gate is
[`friend-beta-release-readiness.md`](friend-beta-release-readiness.md).
