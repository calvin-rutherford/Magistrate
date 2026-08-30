# Friend beta security boundary

Magistrate's initial friend beta is a restricted, single-user deployment. The
server operator configures `MAGISTRATE_BOOTSTRAP_SECRET`,
`MAGISTRATE_BOOTSTRAP_USER_ID`, `MAGISTRATE_SESSION_SCOPES`, and an explicit
`MAGISTRATE_CORS_ORIGINS`. The app exchanges the operator-provided bootstrap
secret for a short-lived, revocable bearer session; no gateway token or runner
credential is included in a frontend bundle.

Sessions are scoped (`read`, `account`, `providers`, `notifications`, `voice`,
and `command`) and every gateway route enforces its scope. The gateway derives
ownership from the session, never from a `user_id` query/body value. Session
bearers must use an Authorization header for HTTP. The events socket accepts a
session in its first application frame, never a query string.

Provider cards report unavailable until real OAuth client configuration and
account data exist. OAuth connect requires an exact allowlisted app redirect;
state is server-side, expiring, principal-bound, and single-use. Do not add
sample identities, provider tokens, runner addresses, or Tailscale credentials
to this repository or the client.

Production deployments must use HTTPS/WSS-facing gateway configuration and an
explicit CORS allowlist. HTTP localhost and auto-session behavior are only for
explicit development/test environments. Production also requires a generated
`MAGISTRATE_SECRET_KEY` for encrypted provider credentials and an absolute,
persistent `MAGISTRATE_DB_PATH` outside the release checkout; the gateway fails
closed if either setting is missing. Keep SQLite backups alongside service
state, not in the frontend or Git checkout. The private runner and Tailscale
network remain deployment concerns and are not part of the friend-beta trust
boundary. Before expanding beyond one restricted operator, replace bootstrap
issuance with a real account/invite provider and add tenant-isolation tests.
