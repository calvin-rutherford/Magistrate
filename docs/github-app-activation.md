# Customer GitHub App activation

Magistrate's repository control plane uses a GitHub App installation, not an
operator CLI session or a customer OAuth token. Until an operator creates and
configures the App, the product reports `BLOCKED_EXTERNAL`; it does not invent
an App ID, slug, key, or webhook secret.

## Provider-console values

Create a GitHub App in the GitHub organization that operates the Magistrate
service. Use these values, substituting the real public Gateway origin:

| GitHub App field | Value |
|---|---|
| Setup URL | `https://<gateway-origin>/api/v1/github/app/callback` |
| Redirect on update | enabled |
| Webhook URL | `https://<gateway-origin>/api/v1/github/webhooks` |
| Webhook active | enabled |
| Repository permissions | Contents: **Read-only**; Pull requests: **Read-only**; Checks: **Read-only**; Metadata: **Read-only** (implicit) |
| Organization/account permissions | none |
| Subscribe to events | Installation, Installation repositories, Repository, Pull request, Check run, Check suite |
| Installation scope | Only this account unless the deployment is intentionally public |

Select **Only select repositories** during customer installation unless the
customer explicitly intends all present and future repositories to be visible.
Magistrate requests a token narrowed to the documented read permissions even if
the App is later misconfigured with broader provider-console permissions.

Generate a private key and a random webhook secret of at least 32 characters.
Record the numeric App ID and provider-assigned App slug exactly; neither value
is supplied by this repository.

## Server configuration

Set these server-only values. Never use an `EXPO_PUBLIC_` variable for them.

```dotenv
GITHUB_APP_ID=<numeric-app-id-from-github>
GITHUB_APP_SLUG=<app-slug-from-github>
GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/magistrate-github-app.pem
GITHUB_APP_WEBHOOK_SECRET=<random-webhook-secret-at-least-32-characters>
MAGISTRATE_GITHUB_APP_CALLBACK_BASE_URL=https://<gateway-origin>
MAGISTRATE_GITHUB_APP_REDIRECT_URIS=magistrate://prs,https://<app-origin>/prs
```

`GITHUB_APP_PRIVATE_KEY` may contain the PEM directly when the deployment
secret manager cannot mount files; configure only one key source. A key file
must be owned for the service and have no group/world permission bits (for
example mode `0600`). The callback base must be HTTPS outside explicit local
development/test mode. Client returns are accepted only by exact match against
`MAGISTRATE_GITHUB_APP_REDIRECT_URIS`; retain `magistrate://prs` for native and
add the production web `/prs` URL when web installation is supported. Wildcard
or caller-selected external redirects are never accepted. Startup fails for
partial, malformed, insecure, or unreadable configuration. Completely absent configuration is permitted only so
the UI can truthfully report the external activation block.

After restart, `GET /api/v1/github/app/status` reports `configured` to an
authenticated account. It never returns the key, webhook secret, App JWT, or an
installation token.

## Customer lifecycle

1. The authenticated client calls `POST /api/v1/github/app/install` with its
   allowed app return URI.
2. Gateway creates a ten-minute, one-use, principal-bound state and returns the
   GitHub installation URL.
3. GitHub returns to the Setup URL with `installation_id` and state. Gateway
   consumes state, verifies the installation with an App JWT, binds it to that
   principal, mints an installation token only in server memory, enumerates all
   authorized repositories, and redirects to the client return URI.
4. `POST /api/v1/github/installations/{id}/reconcile` performs an explicit full
   reconciliation after an operator changes repository selection. Normal
   repository list reads use the persisted webhook projection and do not own an
   execution or process lifecycle.

Every repository read joins the repository to an active installation owned by
the authenticated principal. Numeric repository identity is authoritative, so
rename, transfer, privacy, and default-branch changes update metadata without
changing authorization. Repository removal/deletion and installation deletion
make reads fail closed. Suspension makes the entire installation inaccessible;
unsuspension restores the previously selected rows. Installation-repository
webhooks add and remove access incrementally, and explicit reconciliation
repairs missed delivery.

Webhook bodies are bounded, authenticated with the raw-body HMAC SHA-256
signature, and deduplicated by `X-GitHub-Delivery` plus body digest. Reusing a
delivery ID with different bytes is rejected. Failed processing is retryable;
processed deliveries are idempotent.

## Supported read surface

Authenticated, tenant-qualified routes expose authorized repositories, source
files/directories, branches, commits, pull requests, and commit check runs under
`/api/v1/github/repositories/{repository_id}/...`. The aggregate pull-request
routes support the existing product UI; callers should include
`repository_id` for detail because pull request numbers are repository-local.

Installation access tokens are minted and renewed server-side shortly before
expiry and are never persisted, logged, returned by an API, or placed in a
client URL. This integration observes source-control state only. It does not
start workers, schedule execution, merge pull requests, write source, or own
Gateway/Herdr/Firstmate execution lifecycle.
