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

The base Phase 1 production settings are:

```dotenv
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=/var/lib/magistrate/magistrate.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=<operator-generated-secret>
MAGISTRATE_SECRET_KEY=<generated-fernet-key>
MAGISTRATE_CORS_ORIGINS=https://magistrate.example
OPENAI_API_KEY=<server-side-provider-secret>
MAGISTRATE_MAGI_MODEL=gpt-4o-mini
MAGISTRATE_NATIVE_CHAT_ENABLED=true
MAGISTRATE_LEGACY_CHAT_ENABLED=false
MAGISTRATE_PI_OWNERSHIP_ENABLED=false
```

`OPENAI_API_KEY` must exist only in the mode-`0600` Gateway environment; there
is no `EXPO_PUBLIC_` provider key. The guarded exporter derives the two public
transport booleans from the server flags, preventing a native frontend/legacy
server split. Native defaults to on and legacy defaults to off, but production
deployment still validates that exactly one is enabled. See
[`magi-native-chat-phase1.md`](./magi-native-chat-phase1.md).

The guard requires the environment file to be a service-owned, non-symlink
regular file with mode `0600`; keep the database directory restrictive as well.
Rotate the bootstrap and Fernet keys through the approved secret-management
procedure; never commit them or put them in a frontend build.

## Retained Pi semantic captain channel (rollback only)

Native Phase 1 chat does not invoke Pi ownership. This section documents the
retained legacy rollback subsystem. Within that subsystem, unset and
`1|true|yes|on` enable Pi ownership; only `0|false|no|off` selects terminal
compatibility/recovery mode. An explicitly
empty or unknown literal fails startup. Production should be explicit and must
replace `<uid>` with `id -u` for the actual Gateway/dedicated-Pi service user:

```dotenv
MAGISTRATE_PI_OWNERSHIP_ENABLED=true
MAGISTRATE_PI_RUNTIME_DIR=/run/user/<uid>/magistrate
MAGISTRATE_PI_IPC_KEY_PATH=/run/user/<uid>/magistrate/pi-ownership.key
MAGISTRATE_PI_ADAPTER_SOCKET=/run/user/<uid>/magistrate/pi-ownership.sock
MAGISTRATE_PI_ADAPTER_JOURNAL=/run/user/<uid>/magistrate/pi-ownership.journal
MAGISTRATE_PI_ADAPTER_UID=<uid>
MAGISTRATE_PI_CAPABILITY_TTL_SECONDS=120
MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS=1
MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS=20
MAGISTRATE_PI_RECOVERY_SECONDS=3
```

Put the same values in Gateway's environment and the dedicated captain Pi
service environment. Never put them in frontend configuration. The paths must
be distinct direct children of one service-owned mode-`0700` runtime boundary;
the key, listening socket, and present journal are mode `0600` and owned by the
same effective UID. Gateway startup validates path normalization/length,
ancestor symlinks and replaceable writable boundaries, actual UID, key and
present socket/journal metadata, TTL, and all timeouts. It creates only a
missing final runtime directory and key below an existing trusted parent. A
missing socket means “adapter unavailable,” not a configuration success claim:
Gateway may start for delayed Pi arrival, but all new prompts are durably owned
and return recoverable HTTP 503 without a Herdr send.

Keep four claims separate: **product policy** selects Pi by default;
**configuration** means the startup trust checks passed; **activation** means
the restarted dedicated Pi `MainPID` answered the authenticated probe; and
**durable source ownership** is the per-turn database state fixed at atomic
prepare. Configuration is not activation, and later unavailability or explicit
false mode never releases an already owned turn.

### Guarded legacy activation

When Pi rollback is deliberately enabled, `scripts/deploy_magistrate.sh` treats an absent Pi flag as
enabled and refuses incomplete or duplicate/ambiguous production environment
assignments. For enabled rollout it performs these additional guards without
reading secret contents:

1. verifies the configured adapter UID is the executing service UID and refuses
   any Pi service name that is the Gateway, Herdr, or Firstmate;
2. invokes Gateway's local filesystem validator, creating/validating the key
   without printing it;
3. requires a normalized, symlink-free, service-owned mode-`0600` persistent
   SQLite file, takes an online backup in a likewise normalized service-owned
   mode-`0700` directory outside the checkout, integrity-checks it, and
   writes a mode-`0600` companion containing the exact pre-restart commit;
4. runs `npm ci`, `npm run typecheck`, and `npm test` in `pi-extension/`, then
   builds and checks the frontend before changing Pi package activation;
5. runs `pi install "$(pwd)"`, restarts only
   `magistrate-captain-pi.service` (or the explicit
   `MAGISTRATE_PI_SERVICE`), and proves it loaded the extension with a
   same-UID, signed nonce-bound readiness probe, exact socket peer/MainPID
   match, and an `ss -xl` listener check;
6. only after the fresh adapter is authenticated, restarts
   `magistrate-gateway.service` (or the explicit `MAGISTRATE_SERVICE`), waits
   for HTTP readiness, and prints only fixed prepared/bound/finalized/failed
   and recovery-backlog counts.

The dedicated captain Pi unit is an operator-managed prerequisite because its
model/session launch arguments are deployment-specific. It must load the same
environment file and installed package. `pi install` writes to the selected Pi
configuration scope; use a dedicated service account or a dedicated
`PI_CODING_AGENT_DIR` shared by the install command and that unit. If other Pi
runtimes share the scope, explicitly set
`MAGISTRATE_PI_OWNERSHIP_ENABLED=false` in every non-captain runtime before its
next start—process environment, never pane/workspace naming, selects the one
adapter. Do not point `MAGISTRATE_PI_SERVICE` at a Herdr/Firstmate fleet unit,
and do not use a global Herdr restart as a substitute. Installation does not
affect an existing Pi process; the post-restart authenticated, PID-bound
listener is the activation proof.

Before running the guard, record `git rev-parse HEAD`, `id -u`, both exact unit
names, and the environment variable **names/presence only**. Never print the
environment file, key, capabilities, encrypted dispatch columns, prompts,
credentials, or native entries. After activation verify metadata and the
listener without reading files:

```sh
uid="$(id -u)"
test "$MAGISTRATE_PI_ADAPTER_UID" = "$uid"
stat -c '%a %u %F %n' \
  "$MAGISTRATE_PI_RUNTIME_DIR" \
  "$MAGISTRATE_PI_IPC_KEY_PATH" \
  "$MAGISTRATE_PI_ADAPTER_SOCKET"
test ! -e "$MAGISTRATE_PI_ADAPTER_JOURNAL" || \
  stat -c '%a %u %F %n' "$MAGISTRATE_PI_ADAPTER_JOURNAL"
ss -xl | grep --fixed-strings "$MAGISTRATE_PI_ADAPTER_SOCKET"
sqlite3 "$MAGISTRATE_DB_PATH" \
  "select state,count(*) from pi_semantic_dispatches group by state order by state;"
```

Expected metadata is directory `700`, key/socket/journal `600`, actual UID,
regular key/journal, and Unix socket. The authenticated
`/api/v1/diagnostics/soak` result must say ownership enabled/default-enabled,
whether the flag was defaulted, adapter ready, new captain selection
`pi-semantic`, show fixed bounded state counts/backlog, and report terminal
eligibility only for an actual unowned
legacy turn.

### Rollback without releasing ownership

Stop new submissions first. Wait until every prepared/bound row reaches
`finalized` or explicit `failed` and every final/failed row has
`adapter_acknowledged_at`; an existing journal means receipts have not settled.
Then set `MAGISTRATE_PI_OWNERSHIP_ENABLED=false` in **both** unit environments
and restart only Gateway and the dedicated captain Pi unit. Preserve the
SQLite rows, key, and journal. Never delete ownership state or replay an owned
client message through Herdr. Compatibility mode permits terminal fallback only
for turns that were genuinely unowned before dispatch. Restore the verified
whole-database backup only for an emergency whole-deployment rollback, never to
surgically remove dispatch rows.

The full protocol/recovery matrix is
[`pi-semantic-ownership-v1.md`](./pi-semantic-ownership-v1.md); physical-device
proof uses [`pi-ownership-live-acceptance.md`](./pi-ownership-live-acceptance.md).

The read-only Firstmate snapshot also needs the service account's trusted tool
directories (including the installed `herdr`, `tasks-axi`, and `quota-axi`) on
its subprocess `PATH`. By default the Gateway derives that path from the
service `PATH`, retaining only absolute, existing directories whose ownership
chain belongs to root or the service user and contains no symlink or unsafe
world-writable component. Empty, relative, missing, and untrusted entries are
excluded; if no trusted entry remains, Firstmate is unavailable. To pin a
stricter deployment contract, set a colon-separated list such as:

```dotenv
MAGISTRATE_FIRSTMATE_TOOL_PATH=/opt/firstmate/bin:/usr/local/bin:/usr/bin
```

An explicitly configured list fails closed if any entry is invalid. Use the
actual service-account installation directories; do not encode one operator's
home as a product default.

The snapshot's Firstmate-owned lifecycle tools also need their account/runtime
`HOME` to resolve their own no-mistakes run state. Owner installs conventionally
place `FM_HOME` directly below that home (for example, `<account-home>/firstmate`),
so when no override is set Gateway derives only the direct parent of the
resolved `FM_HOME`. A Friend runtime with a deeper contained `FM_HOME`, or any
other layout, must bind it explicitly:

```dotenv
MAGISTRATE_FIRSTMATE_RUNTIME_HOME=/var/lib/magistrate-runtime
```

The runtime home must be an absolute existing directory owned by root or the
Gateway service identity, must contain the selected `FM_HOME`, and may have no
symlink or unsafe world-writable path component. `/` and cross-runtime bindings
are rejected. An invalid explicit value makes Firstmate unavailable rather than
falling back to an ambient or derived home. The child receives this validated
`HOME` plus only the existing bounded allowlist of process basics; it does not
inherit Gateway credentials or unrestricted environment variables.

Run `scripts/deploy_magistrate.sh` from a trusted shell for a manual update. The
script fetches `origin/main`, refuses dirty or divergent checkouts, performs a
fast-forward-only update, applies the enabled Pi guards above, runs the
supported `npx expo export -p web` build, and checks that `index.html`,
`chat.html`, and `voice.html` exist. When Pi ownership is enabled it first
restarts and proves only the configured dedicated captain Pi unit/socket; it
then restarts `magistrate-gateway.service` and polls the HTTP process for up to
30 seconds. This order ensures a newly introduced authenticated probe never
meets an old loaded extension during an upgrade. The local URL
defaults to `http://127.0.0.1:8000/api/v1/health` and can be overridden with
`MAGISTRATE_READINESS_URL`; timeout, poll interval, and per-request timeout are
configurable with `MAGISTRATE_READINESS_TIMEOUT_SECONDS`,
`MAGISTRATE_READINESS_INTERVAL_SECONDS`, and
`MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS`; Pi arrival uses
`MAGISTRATE_PI_READINESS_TIMEOUT_SECONDS`. Connection refusal (`HTTP
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

The guarded deployment performs this online backup automatically before every
enabled native rollout (and before retained Pi activation), verifies integrity
and every table's row count, records the exact deployment commit, and writes a
SHA-256 sidecar before build or restart. Missing, symlinked, checkout-local, or
wrong-mode databases refuse deployment. The test harness restores that artifact
and reads a pre-existing legacy conversation row.

The gateway's `init_db()` uses additive `CREATE TABLE IF NOT EXISTS` schema
initialization. Phase 1 adds `magi_conversations`, `magi_messages`,
`magi_replay_changes`, and `magi_chat_metrics`; it does not rename, rewrite, or
delete `conversation_turns` / `conversation_messages`. Restarting with the same
absolute path therefore preserves legacy conversations, profiles, provider
credentials, execution settings, and bearer-session rows. Verify
expiry/revocation and restart persistence with the gateway suite and trusted
smoke. To restore, stop the user unit, preserve the failed database, copy the
selected backup back to `MAGISTRATE_DB_PATH`, restore ownership/mode, start the
unit, and rerun the smoke. Never replace the path with a checkout-local file or
delete it as part of a frontend deploy.

## Automatic demo redeploy (disabled)

Automatic GitHub demo redeployment is intentionally disabled under the captain's
standing instruction. `.github/workflows/deploy-demo.yml` has no `push` trigger,
so merges and other changes to `main` cannot start an SSH deployment. It remains
an explicitly manual-only workflow: an operator must dispatch it deliberately
and check its confirmation input. Keep this posture until automatic deployment
is separately authorized and the repository deployment secrets are deliberately
configured; this change does not configure or change that secret contract.

Git history explains the audit finding: before PR #44, these deployment docs
explicitly said that no unattended timer was installed because a pull could
leave the service and assets out of sync. PR #44 then introduced the workflow
with a `push`-to-`main` trigger, and PR #50 improved its readiness polling
without removing that trigger. No subsequent repository change represented the
later disable instruction, so the active trigger remained in the shipped
workflow.

A deliberate manual GitHub dispatch still uses the existing guarded SSH path and
requires the existing repository Actions secret contract:

- `MAGISTRATE_DEPLOY_HOST` — the demo host name or Tailscale address
- `MAGISTRATE_DEPLOY_USER` — the unprivileged service account
- `MAGISTRATE_DEPLOY_SSH_KEY` — a dedicated SSH private key authorized only for
  the deployment account
- `MAGISTRATE_DEPLOY_KNOWN_HOSTS` — the pinned `known_hosts` entry

Missing secrets, unavailable host access, a dirty checkout, or a
non-fast-forward/divergent checkout fail closed; no reset or force push is
attempted. The workflow's concurrency group prevents overlapping updates.

The approved private Tailscale deployment process remains the preferred manual
recovery path. Log into the demo host through the approved Tailscale SSH path and
run:

```sh
cd /home/spectre/firstmate/projects/Magistrate-deploy
git status --short
/home/spectre/firstmate/projects/Magistrate-deploy/scripts/deploy_magistrate.sh
```

Resolve any reported dirty/divergent state by preserving and reviewing its
unique work, then retry. Do not use `git reset --hard`, `git stash`, or a force
push. Verify the configured HTTPS gateway health URL with an issued Bearer
session. Do not put the deployment host, runner address, or bootstrap secret in
Git.
