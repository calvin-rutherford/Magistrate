# OAuth transaction security

Gateway OAuth connects use an opaque, cryptographically random state value. The
gateway stores only its SHA-256 hash together with the authenticated request's
principal, provider, exact application redirect, and a short expiry. A callback
must match the provider and consume the transaction atomically before any token
exchange or credential persistence. Missing, malformed, unknown, expired,
replayed, and mismatched state is rejected without a redirect because no target
is trusted until the transaction is validated.

The default application redirect is `magistrate://account`. Redirects are exact
matches from `MAGISTRATE_OAUTH_REDIRECT_URIS`, a comma-separated environment
allowlist. If the variable is unset, only the default native-app redirect is
allowed. The allowlist must be explicit in local development; loopback HTTP
targets (`localhost`, `127.0.0.1`, or `::1`) are accepted only when explicitly
listed. Public HTTP, browser-executable schemes, query strings, fragments, and
userinfo-bearing URLs are rejected. HTTPS and custom app schemes are also
required to be exact allowlist entries.

The state lifetime defaults to 10 minutes and can be shortened or configured up
to one hour with `MAGISTRATE_OAUTH_STATE_TTL_SECONDS`. This flow does not make
the callback endpoint require the app's gateway header: the provider redirect
has no such header, and the one-time server-side transaction is the callback's
integrity boundary. Replacing the existing shared-token principal model remains
the responsibility of `SEC-AUTH`; this package never trusts a callback-supplied
user or redirect value.
