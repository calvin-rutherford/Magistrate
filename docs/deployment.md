# Production deployment

The production gateway and its static Expo web build live together in
`/home/spectre/firstmate/projects/Magistrate-deploy`. The user unit's
`WorkingDirectory`, `EnvironmentFile`, and `MAGISTRATE_DIST_DIR` all point at
that persistent checkout. The deployment checkout owns its `.env`; keep that
file out of Git and do not copy it from a dirty development checkout during a
routine update. Production startup is fail-closed: the file must set
`MAGISTRATE_ENV=production`, `MAGISTRATE_DB_PATH` to an absolute path outside
the checkout, `MAGISTRATE_BOOTSTRAP_SECRET`, `MAGISTRATE_SECRET_KEY`, and
`MAGISTRATE_CORS_ORIGINS` (HTTPS origins only). The SQLite file and its
rollback/backup copies therefore survive frontend exports and Git updates.

A minimal deployment-only configuration is:

```dotenv
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=/var/lib/magistrate/magistrate.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=<operator-generated-secret>
MAGISTRATE_SECRET_KEY=<generated-fernet-key>
MAGISTRATE_CORS_ORIGINS=https://magistrate.example
```

Set restrictive permissions on the env file and database directory. Rotate the
bootstrap and Fernet keys through the approved secret-management procedure;
never commit them or put them in a frontend build.

Run `scripts/deploy_magistrate.sh` from a trusted shell for a manual update. The
script fetches `origin/main`, refuses dirty or divergent checkouts, performs a
fast-forward-only update, runs the supported `npx expo export -p web` build,
checks that `index.html`, `chat.html`, and `voice.html` exist, restarts
`magistrate-gateway.service`, and polls the HTTP process for up to 30 seconds
(the local URL defaults to `http://127.0.0.1:8000/api/v1/health` and can be
overridden with `MAGISTRATE_READINESS_URL`; timeout, poll interval, and
per-request timeout are configurable with `MAGISTRATE_READINESS_TIMEOUT_SECONDS`,
`MAGISTRATE_READINESS_INTERVAL_SECONDS`, and
`MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS`). Connection refusal (`HTTP
000`) during the expected systemd restart window is retried; timeout output
includes the last HTTP response and systemd state. Readiness accepts 2xx,
401, or 403 responses, so it verifies application reachability rather than
mistaking a merely existing process for a healthy deployment. It also rejects
missing production auth settings, wildcard/non-HTTPS CORS, and checkout-local
SQLite paths before running the build. Validate the authenticated path with an
operator-issued Bearer session during the release smoke check.
It never resets, stashes, or discards deployment-only commits.

GitHub Actions must not receive the bootstrap secret. For the complete trusted-host
smoke, run this on the deployment host after the update (it reads `gateway/.env`
without printing the secret or the issued bearer):

```sh
MAGISTRATE_TRUSTED_SMOKE=1 \
  MAGISTRATE_DEPLOY_DIR=/home/spectre/firstmate/projects/Magistrate-deploy \
  /home/spectre/firstmate/projects/Magistrate-deploy/scripts/deploy_magistrate.sh
```

That smoke proves session issuance, protected session validation, authenticated
`/health`, and authenticated `/agents` (the Herdr-backed application path). The
Actions workflow separately performs only secret-free, unauthenticated
reachability checks (plus the static frontend); it never reads the bootstrap
secret or handles a bearer token. A Tailscale HTTPS reverse proxy may remain
the configured external health/smoke URL, and must continue to pass HTTP
`Upgrade`/`Connection` headers so the gateway's WSS `/api/v1/events` endpoint
keeps working.

## SQLite backup and migration

`MAGISTRATE_DB_PATH` is deployment state, not release state. Keep its directory
owned by the service account and mode `0700`, and the database mode `0600`.
Before an upgrade or key rotation, make an online SQLite backup and record its
revision; the SQLite backup API is safe while the service is running:

```sh
set -eu
DB=/var/lib/magistrate/magistrate.sqlite3
BACKUP=/var/lib/magistrate/backups/magistrate-$(date -u +%Y%m%dT%H%M%SZ).sqlite3
install -d -m 700 /var/lib/magistrate/backups
python3 - "$DB" "$BACKUP" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as backup:
    source.backup(backup)
PY
chmod 600 "$BACKUP"
sqlite3 "$BACKUP" 'pragma integrity_check;'
```

The gateway's `init_db()` uses additive `CREATE TABLE IF NOT EXISTS` schema
initialization, so restarting it with the same absolute path preserves profiles,
provider credentials, execution settings, and bearer-session rows. Verify
expiry/revocation and restart persistence with the gateway suite and trusted
smoke. To restore, stop the user unit, preserve the failed database, copy the
selected backup back to `MAGISTRATE_DB_PATH`, restore ownership/mode, start the
unit, and rerun the smoke. Never replace the path with a checkout-local file or
delete it as part of a frontend deploy.

## Automatic demo redeploy

`.github/workflows/deploy-demo.yml` runs on every push to `main` (the signal
GitHub emits after a pull request is merged). Configure these repository Actions
secrets before enabling it:

- `MAGISTRATE_DEPLOY_HOST` — the demo host name or Tailscale address
- `MAGISTRATE_DEPLOY_USER` — the unprivileged service account
- `MAGISTRATE_DEPLOY_SSH_KEY` — a dedicated SSH private key authorized only for
  the deployment account
- `MAGISTRATE_DEPLOY_KNOWN_HOSTS` — the pinned `known_hosts` entry

The workflow uses only those repository-provided host credentials and invokes
the guarded script on the persistent checkout. Missing secrets, unavailable host
access, a dirty checkout, or a non-fast-forward/divergent checkout fail closed;
no reset or force push is attempted. The workflow's concurrency group prevents
overlapping updates.

If automatic deployment is unavailable, log into the demo host through the
approved SSH path and run:

```sh
cd /home/spectre/firstmate/projects/Magistrate-deploy
git status --short
/home/spectre/firstmate/projects/Magistrate-deploy/scripts/deploy_magistrate.sh
```

Resolve any reported dirty/divergent state by preserving and reviewing its
unique work, then retry. Do not use `git reset --hard`, `git stash`, or a force
push. If host credentials are unavailable, the remaining manual step is to have
the deployment owner configure the four repository secrets (or run the command
from a trusted host) and then verify the configured HTTPS gateway health URL
with an issued Bearer session. Do not put the deployment host, runner address,
or bootstrap secret in Git.
