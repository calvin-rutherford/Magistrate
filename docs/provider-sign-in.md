# Apple and Google sign-in

Magistrate supports provider-backed account identity on the iPhone client and web. The Gateway remains the identity authority: clients collect an assertion, while the Gateway verifies its signature, issuer, audience, lifetime, one-time nonce, challenge ownership, and replay status before mapping the provider subject to the existing `user_profiles` and `connected_accounts` tables.

## Session boundary

- Login identities are `connected_accounts.account_kind = 'login'`; ordinary OAuth integrations remain `account_kind = 'oauth'` and never become login authority.
- Access bearers are short-lived `gateway_sessions` rows.
- Native receives a rotating `mgr_` refresh token. `GatewaySessionStorage` stores it only in iOS Keychain/Android Keystore through Expo SecureStore.
- Web receives refresh authority only in the `magistrate_provider_refresh` Secure, HttpOnly, SameSite=Lax cookie. JavaScript persists only a non-authoritative “session may exist” marker and obtains a fresh bearer after reload.
- Every refresh consumes the presented token and revokes prior family bearers. Families are bound to their native-or-web delivery channel; channel mismatch or reuse of a consumed token retires the entire family and push delivery.
- Logout retires the provider family, all of its bearers, push registration, and the browser cookie. Provider-account disablement also invalidates bearer validation.
- Apple and Google subjects are never merged by email. A stable provider subject maps back to the same Magistrate principal; linking a second provider requires the authenticated link action.

The client-platform challenge is fail-closed. Web challenges require an HTTP(S) redirect and return cookie authority; Google native challenges require an allowlisted custom-scheme redirect. Platform-specific audiences are checked when configured, preventing a web assertion from selecting the native JSON refresh channel. Apple authorization receives the SHA-256 digest of the raw one-time nonce, while the raw nonce remains bound to the Gateway challenge and exchange.

## Gateway configuration

An entirely absent provider is unavailable. Production startup rejects partial iPhone/web configuration.

```text
# Apple native audience (the committed iOS bundle identifier)
MAGISTRATE_APPLE_CLIENT_IDS=io.magistrate.cockpit

# Apple web code exchange
MAGISTRATE_APPLE_SERVICE_ID=com.example.magistrate.web
MAGISTRATE_APPLE_TEAM_ID=...
MAGISTRATE_APPLE_KEY_ID=...
MAGISTRATE_APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."

# Google platform audiences
MAGISTRATE_GOOGLE_WEB_CLIENT_IDS=...apps.googleusercontent.com
MAGISTRATE_GOOGLE_IOS_CLIENT_IDS=...apps.googleusercontent.com

# Exact comma-separated callback allowlist. Include the web origin callback and
# the reversed Google iOS client scheme used by the built app.
MAGISTRATE_AUTH_REDIRECT_URIS=https://app.example.com/,com.googleusercontent.apps....:/oauthredirect
```

`MAGISTRATE_GOOGLE_CLIENT_IDS` may contain additional accepted Google audiences. Platform-specific production IDs still belong in the web/iOS variables above. Optional controls are:

```text
MAGISTRATE_PROVIDER_REFRESH_TTL_SECONDS=2592000
MAGISTRATE_PROVIDER_SESSION_SCOPES=read,account,providers,notifications,voice,command
```

The refresh lifetime must be between one hour and 90 days. `response` is never valid in provider-issued scopes. Keep the Apple private key and every provider secret only in the Gateway secret store.

For web, deploy the Gateway on the same site as the frontend (same-origin is preferred) so browser privacy controls permit the HttpOnly cookie. Production callback and app origins must use HTTPS.

## Expo SDK 57 client configuration

The project uses the SDK 57-compatible `expo-apple-authentication`, `expo-auth-session`, `expo-web-browser`, and `expo-crypto` packages. `ios.usesAppleSignIn` and the Apple config plugin are committed.

Set these public build identifiers; none is a credential:

```text
EXPO_PUBLIC_APPLE_SERVICE_ID=com.example.magistrate.web
EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID=...apps.googleusercontent.com
EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID=...apps.googleusercontent.com
EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID=com.googleusercontent.apps....
```

The reversed Google client ID is added to Expo's app schemes at build time. Apple native uses bundle identifier `io.magistrate.cockpit`. Rebuild the native binary after changing entitlement, bundle, OAuth client, or URL-scheme configuration.

## Rollout and verification

`gateway/app/db.py` applies additive columns/tables during `init_db()`. Back up the SQLite database before rollout, deploy the Gateway first, and then ship clients built with matching IDs and redirects.

Automated coverage verifies signed assertions, audience/time/nonce checks, replay protection, stable principal mapping, web cookie-only continuity, native rotation/reuse revocation, logout, and cross-platform redirect refusal. A release still requires real Apple and Google test accounts against the registered deployment callbacks; repository tests cannot substitute for provider-console and physical-iPhone evidence.
