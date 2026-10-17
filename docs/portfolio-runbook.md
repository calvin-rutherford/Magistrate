# Personal candidate runbook

## Status and one process owner

This is an operator-reviewed procedure, **not a record of commands executed on a
live service**. Personal execution is currently unavailable. Dependency, runtime,
producer, deployment and device gates must remain visible.

The dedicated user service `magistrate-gateway.service` is the only production
Gateway process owner. Its entry point is `gateway/app/main.py` through Uvicorn;
use `runtime/magistrate-gateway.service.example` and the security operations
runbook. SQLite requires one Gateway instance. Do not start a second background
Gateway, Herdr poller, Pi/Django launcher, or competing reconciliation supervisor.
Firstmate remains the sole supported default execution runtime, separately
operator-owned and pinned by `runtime/firstmate-producer.lock.json`.

The Gateway template has `Restart=on-failure`, `RestartSec=5`, one Uvicorn worker,
and process/memory limits. An operator must review explicit systemd start-rate
limits before activation; no installed-unit settings were inspected here. There
is no second watchdog. Gateway startup performs one bounded intake/cancellation
recovery pass and settlement recovery; notification receipt polling does not
supervise execution.

**Remaining startup gate:** `scripts/install_firstmate_producer.sh activate`
requires `--sessions-reloaded`; it does not start/supervise a runtime. The existing
bridge writes deterministic backlog/inbox entries for a separately running
Firstmate supervisor. This repository does not establish unattended supervisor
startup/restart with the personal hard-limit envelope. Do not hide an interactive
terminal prerequisite behind Gateway readiness. A reviewed service owner and
bounded runtime startup/restart contract must close that gate before activation.
The existing verification commands (not run against a live installation here) are:

```sh
bash scripts/install_firstmate_producer.sh verify --root /absolute/reviewed/pinned-runtime
bash scripts/install_firstmate_producer.sh ready --root /absolute/reviewed/pinned-runtime --fm-home /absolute/private/runtime-home
```

## Configuration review

Read `.env.example` and [production security](production-security-operations.md).
Retain production secrets, provider allowlists, exact CORS, protected external
storage, TLS and normal session issuance/revocation. Do not print the environment.
Personal mode additionally requires these **server-only** settings:

```dotenv
MAGISTRATE_DEPLOYMENT_MODE=personal
MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID=<exact-existing-authenticated-principal>
MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS=1
MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD=<positive-integer>
MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD=<positive-integer-at-least-objective-limit>
```

Values are placeholders to review, not a usable environment file. Worker count
must be 1 or 2. Do not enable hosted execution simultaneously. Select public-SaaS
explicitly/default to keep its existing commercial policy. Switching modes does
not discard old reservations or authorize their replay. Personal onboarding does
not mark an unpaid subscription complete. Signed Stripe webhook/history behavior
is retained; checkout/portal/account credit routes return a detached-mode refusal.

**No flag opens the current personal runtime gate.**
`personal_runtime_limits_unavailable` means no subprocess publication occurred on
that new claim, not “configuration accepted, worker running.” The measured ledger
is not a sandbox or a scheduler. Require a separately reviewed pinned runtime
that actually enforces concurrency and per-objective spend, plus producer support
for run/request/scope/expiry-bound decisions. Never substitute prompts or an
operator acknowledgement for those capabilities.

## Read-only diagnosis

Use an explicitly selected, authorized, consistent SQLite snapshot—never an
operator DSN or a guessed default path. PostgreSQL is intentionally unsupported
by this command. Snapshot creation itself is a separate authorized operation.

```sh
python3 scripts/magistrate_diagnostics.py \
  --db /absolute/private/verified-snapshot.sqlite3 \
  --owner <exact-principal-id>
```

The command uses `mode=ro&immutable=1` and `query_only`, imports no Gateway code,
and refuses missing/wrong/symlinked files or outstanding WAL/journal. It reports
checkout revision, schema observations, scoped unrevoked bearer counts, pending
decisions, allowlisted/redacted task errors, and distinct push configuration and
provider evidence. Runtime selection is the candidate's default, not an
observation of the installed service. EAS-setting presence describes only the
diagnostic process, not its service environment. It does not migrate, create
tables, load secrets, probe the runtime, or authenticate a real session. Bearer
row counts are not provider refresh health. Receipt handoff is not phone delivery/viewing. Keep snapshot path
metadata private. A refusal must not be “fixed” by deleting WAL files.

After a separately authorized deployment, these repository-derived **read-only
operator commands** inspect the configured service:

```sh
systemctl --user status magistrate-gateway.service --no-pager
journalctl --user -u magistrate-gateway.service -n 100 --no-pager
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
```

Readiness is only HTTP 200; 401/403 prove neither readiness nor provider health.
Review logs privately; do not attach credentials, message content or full logs to
a public PR. `/readyz` does not execute a model or probe Firstmate.

## Recovery choices

| Observation | Safe action |
|---|---|
| Saved prompt, response interrupted | Keep canonical user row; use explicit response retry, not another objective. |
| Intake timeout/wake failure | Treat dispatch as uncertain. Reconcile deterministic identity through an authorized write-side operation; retain capacity. Never blindly resubmit/cancel/release. |
| Accepted queue row, no producer event | Show queued/unobserved, not running. Reads do not start a worker. |
| Terminal event committed, settlement interrupted | Durable settlement recovery consumes the exact measured event once, even after logout/mode change. Missing cost is not zero. |
| Decision stale/expired/repository revoked | Read-only navigation/refusal; fetch current owner-authorized state. Do not send the notification's snapshot as approval. |
| Cancellation requested | Continue to show requested until a structured terminal event observes cancellation. |
| Push missing/denied/offline | Preserve Attention; inspect registration, provider acceptance and receipt separately. No fabricated local success notification. |

## Deploy and rollback: authorization required, not run here

The update-only `scripts/deploy_magistrate.sh` locks and refuses dirty/diverged
checkouts, preserves reviewed ahead commits, performs preflight and backup before
startup migration, exports web, restarts only the dedicated unit, and requires
`/readyz` 200. Its default release path is a script default, **not observed live
infrastructure**. Review selected catalog/Stripe settings too: production
preflight remains authoritative and is not bypassed by this document.

```sh
MAGISTRATE_DEPLOY_DIR=/absolute/reviewed/release \
MAGISTRATE_SERVICE=magistrate-gateway.service \
bash scripts/deploy_magistrate.sh
```

Do not run it for this blocked candidate. Before any future activation, record
the exact candidate and previous native revision, private environment/key version,
DB/object snapshot IDs and hashes, frontend artifact, and rollback owner. Schema
10 is additive, but downgrading the binary is not proof of safe data rollback.

For a future authorized rollback, preserve the failed release, stop traffic and
quiesce the dedicated unit, select a clean immutable directory for the exact
previous reviewed native commit, and verify schema compatibility. If restoration
is needed, restore to a **new** private DB destination with the matching object
snapshot/key version; never overwrite the live DB or carry a stale WAL. Update
the reviewed service paths/environment, restart that one unit and require
readiness plus authenticated read smoke before reopening. Old backups may revive
revoked authority: revoke/reissue affected sessions/grants. No automatic rollback,
legacy transport fallback, or destructive `git reset` is supported.

Repository storage syntax (requires explicit operator approval):

```sh
python3 gateway/scripts/storage_ops.py backup /private/state/app.sqlite3 /private/backups/release.sqlite3
python3 gateway/scripts/storage_ops.py restore /private/backups/release.sqlite3 /private/restore/new.sqlite3
```

See [security operations](production-security-operations.md) for private modes,
manifest verification, PostgreSQL recovery and complete retention limitations.

## iPhone build/acceptance: separate gate

Follow [Friend Beta readiness](friend-beta-release-readiness.md), the native lane's
[evidence guide](native-loop-evidence.md), [iOS entry guide](ios-voice-shortcuts.md),
and `frontend/eas.json`.
Resolved public HTTPS endpoint, real EAS/Apple/APNs configuration, signing and
privacy review precede a physical build. From a clean approved candidate:

```sh
cd frontend
npm run beta:preflight -- --profile preview
npx eas-cli@23.0.0 build --platform ios --profile development
npx eas-cli@23.0.0 build --platform ios --profile preview
# Only after separate production/store authorization:
npm run beta:preflight -- --profile production
npx eas-cli@23.0.0 build --platform ios --profile production
npx eas-cli@23.0.0 submit --platform ios --profile production --id <reviewed-build-id>
```

These commands were **not executed**. Internal preview is not TestFlight. The
owner/operator must perform the following separately authorized actions:

1. Confirm the EAS project/owner and `io.magistrate.cockpit` Apple app record;
   register the physical device for internal/ad-hoc distribution, review signing
   certificate/provisioning profile, push entitlement and APNs key in the Expo/Apple
   consoles. Keep keys out of source, logs and public variables. Configure the
   matching profile's public HTTPS Gateway endpoint, never a private server address.
2. For local native compilation/install on macOS with Xcode, use the native lane's
   commands below. Verify the App Intents Swift source is in Compile Sources and
   retain the actual build log; generation alone is not compilation. For an EAS
   internal build, open the reviewed build's Install link/QR on the registered
   iPhone. There is no repository physical-IPA install script; do not use a
   simulator installer as phone evidence. For store distribution set the real
   `submit.production.ios.ascAppId`, obtain separate submission approval, and
   install the exact processed build through TestFlight.
3. Streaming voice needs a separately reviewed/compiled native WebRTC or microphone
   PCM transport and a server-authorized canonical voice-session bridge. Approve
   provider capability, credential scope/TTL, cost ceilings and tool sideband
   authorization before issuing any short-lived credentials. Persistent provider
   keys remain server-only. Current record/transcribe/TTS is turn-based only.
4. In iPhone Settings grant/revoke microphone and notification permission; in app
   Settings enable Attention updates, review quiet hours, enable native push and
   explicitly send the owner test. Record registration, provider acceptance,
   receipt, actual phone display and owner viewing independently. Kill the app,
   tap a real request-bound push, restore the session and compare the current
   decision/revision. A stale/expired tap must remain read-only. The canonical
   decision UI adapter is still an open code gate, not passed by this checklist.
5. On supported hardware choose Settings → Action Button → Shortcut → **Start
   Voice**. In Shortcuts add that action, name the shortcut **Wake Magi**, and
   invoke it through Siri. Verify consent and foreground session restoration;
   this is not a wake word or a power-button hook. Check interruptions, headset
   routes, lock/background stop, Reduce Motion and cold/warm repeated entry.
6. Create a disposable repository with only a README and a small test, no private
   data or deployment credentials. Grant the owner's GitHub App installation access
   to **only** that repo, reconcile it server-side, and bind exactly one active
   repository to the owner's project. Freeze a one-file objective and acceptance
   test; verify base/result commit and reviewable diff. No live objective may run
   until the runtime enforcement/producer/startup gates are resolved.

```sh
# macOS + Xcode + reviewed signing only; not run in this integration:
cd frontend
npx expo prebuild --platform ios --no-install
npx expo run:ios --device
```

Record exact build/device/iOS/network/time and run [the demo's physical rows](portfolio-demo-guide.md)
and [DEAT-001](DEAT-001.md). No browser/simulator/provider receipt can pass those rows.
