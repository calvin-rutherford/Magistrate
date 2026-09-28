# Apple and Google sign-in

Magistrate supports provider-backed account identity on the iPhone client and web. The Gateway remains the identity authority: clients collect an assertion, while the Gateway verifies its signature, issuer, audience, lifetime, one-time nonce, challenge ownership, and replay status before mapping the provider subject to the existing `user_profiles` and `connected_accounts` tables.

## Session boundary

- Login identities are `connected_accounts.account_kind = 'login'`; ordinary OAuth integrations remain `account_kind = 'oauth'` and never become login authority.
- Access bearers are short-lived `gateway_sessions` rows.
- Native receives a rotating `mgr_` refresh token. `GatewaySessionStorage` stores it only in iOS Keychain/Android Keystore through Expo SecureStore.
- Web receives refresh authority only in the `magistrate_provider_refresh` Secure, HttpOnly, SameSite=Lax cookie. JavaScript persists only a non-authoritative “session may exist” marker and obtains a fresh bearer after reload.
- Every refresh consumes the presented token and revokes prior family bearers. Families are bound to their native-or-web delivery channel; channel mismatch or reuse of a consumed token retires the entire family and push delivery.
- Logout retires the provider family, all of its bearers, push registration, and the browser cookie. Provider-account disablement also invalidates bearer validation.
- Apple and Google subjects are never merged by email. A stable provider subject maps back to the same Magistrate principal; linking a second provider requires the authenticated link action. Account Settings lists those login methods, refuses removal of the current or last method, and makes a removed subject unusable until it is re-linked from another authenticated method.

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

## First-run onboarding and billing

A provider principal is gated from protected product routes by durable `account-onboarding.v1` state. The additive migration backfills existing connected login principals so pre-deploy sessions and fresh sign-ins cannot observe different gates. Onboarding resumes, in order, through welcome acknowledgement, profile naming, a genuinely credential-backed GitHub OAuth connection, and an active/trialing Stripe subscription before entering Magi. GitHub and billing completion are derived from canonical credential/subscription rows, not browser-return parameters. A checkout redirect never grants access; only a timestamp-bound, HMAC-verified Stripe webhook can change subscription state.

Stripe is optional only in the sense that an entirely absent integration leaves onboarding truthfully blocked. Partial or unsafe configuration refuses Gateway startup:

```text
MAGISTRATE_STRIPE_SECRET_KEY=sk_live_...
MAGISTRATE_STRIPE_WEBHOOK_SECRET=whsec_...
MAGISTRATE_STRIPE_PRICE_ID=price_...
MAGISTRATE_BILLING_SUCCESS_URL=https://app.example.com/?billing=success
MAGISTRATE_BILLING_CANCEL_URL=https://app.example.com/?billing=cancel
MAGISTRATE_BILLING_PORTAL_RETURN_URL=https://app.example.com/account
```

Register `POST https://<gateway>/api/v1/billing/webhook` in Stripe for `checkout.session.completed` and `customer.subscription.created`, `.updated`, and `.deleted`. The checkout embeds the opaque Magistrate user ID in Checkout and Subscription metadata; do not edit that metadata in provider automation. Checkout and customer-portal URLs are always server-created.

GitHub onboarding uses the existing authenticated OAuth transaction boundary. Add the exact app and native return locations to `MAGISTRATE_OAUTH_REDIRECT_URIS`, and register the Gateway's `/api/v1/auth/github/callback` in the GitHub OAuth App. The operator bootstrap endpoint remains a curl/automation recovery boundary; production customer UI accepts only `mgb_` Friend Beta invitations and never submits arbitrary text to bootstrap issuance.

## Rollout and verification

`gateway/app/db.py` applies the additive onboarding/billing migration through the shared SQLite/PostgreSQL persistence seam during `init_db()`. Take a transactionally consistent database backup before rollout, deploy the Gateway first, and then ship clients built with matching IDs and redirects.

Automated coverage verifies signed assertions, audience/time/nonce checks, replay protection, stable principal mapping, authenticated linking, recovery-method removal, web cookie-only continuity, native rotation/reuse revocation, logout, cross-platform redirect refusal, resumable onboarding, and signed billing state. A release still requires real Apple, Google, GitHub, and Stripe test/live accounts against the registered deployment callbacks plus a physical-iPhone run; repository tests cannot substitute for provider-console evidence.

### `BLOCKED_EXTERNAL` activation checklist

Repository-controlled implementation is complete without committing fake identifiers or secrets. Release authority remains blocked externally until the owner records evidence for all of the following:

1. Apple App ID `io.magistrate.cockpit`, Sign in with Apple capability, Service ID, key, web return origin, and real native/web account runs.
2. Google iOS and web OAuth clients, consent screen publication/test-user policy, exact reversed iOS URL scheme, exact web origin, and real native/web account runs.
3. GitHub OAuth App callback plus every exact `MAGISTRATE_OAUTH_REDIRECT_URIS` return location used by released clients.
4. Stripe live product/price, webhook endpoint and signing secret, customer portal configuration, and observed signed `active` plus cancellation events.
5. EAS production variables (`EXPO_PUBLIC_GATEWAY_URL`, Google public client identifiers), a real App Store Connect app record, and physical TestFlight login/restart/logout/recovery evidence.
