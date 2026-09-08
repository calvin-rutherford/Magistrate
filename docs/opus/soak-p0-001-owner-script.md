# SOAK-P0-001 physical-owner script

**Status in this PR: `NOT_RUN — OWNER_REQUIRED`. No physical-iPhone result is claimed.** This script is the remaining live acceptance procedure after review. It deliberately requires owner deployment, Apple signing, the owner's authenticated session, and existing push-provider configuration; none are supplied or exercised by the implementation lane.

## 1. Freeze and record the exact candidate

Run in a fresh worktree. Set the PR URL to this pinned-producer Magistrate PR, never to a branch name alone:

```bash
set -euo pipefail
set +x
export MAGISTRATE_PR_URL='https://github.com/calvin-rutherford/Magistrate/pull/91'
export CANDIDATE_SHA="$(gh pr view "$MAGISTRATE_PR_URL" --json headRefOid --jq .headRefOid)"
test "${#CANDIDATE_SHA}" -eq 40
printf 'SOAK-P0-001 candidate=%s observed_at=%s\n' \
  "$CANDIDATE_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee soak-p0-001-candidate.txt
git fetch origin "$CANDIDATE_SHA"
git switch --detach "$CANDIDATE_SHA"
test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

PR #91 is the candidate authority. If its head changes, this is a new candidate and all evidence below restarts.

## 2. Install and activate the reviewed producer

As the same Unix account that owns the deployed Firstmate/Gateway runtime state, install code without touching an operational home:

```bash
export PRODUCER_ROOT="$HOME/.local/share/magistrate/firstmate-producer/2af0d17014cb2e244aa441bfe6df16c4f630475b"
scripts/install_firstmate_producer.sh install --root "$PRODUCER_ROOT"
scripts/install_firstmate_producer.sh verify --root "$PRODUCER_ROOT"
```

The current development session under `/home/spectre/firstmate` is not an assumed rollout target and must not be edited/reloaded in place. The Firstmate owner must obtain the deployed Gateway service's exact `FM_HOME` from its protected environment and identify only the sessions attached to that runtime:

```bash
export DEPLOYED_FM_HOME='<exact FM_HOME from deployed Gateway service environment>'
test -d "$DEPLOYED_FM_HOME"
test "$DEPLOYED_FM_HOME" != "$PRODUCER_ROOT"
```

Quiesce applicable deployed-runtime work, then reload **every applicable** primary/worker Pi session through the pinned root with `FM_ROOT_OVERRIDE=$PRODUCER_ROOT`. Installation does not prove this reload happened. If any applicable session remains on older code, stop. Only after the Firstmate owner explicitly confirms the reload:

```bash
scripts/install_firstmate_producer.sh activate \
  --root "$PRODUCER_ROOT" --fm-home "$DEPLOYED_FM_HOME" --sessions-reloaded
scripts/install_firstmate_producer.sh ready \
  --root "$PRODUCER_ROOT" --fm-home "$DEPLOYED_FM_HOME"
```

Configure the candidate Gateway with the existing `FM_HOME=$DEPLOYED_FM_HOME`, `MAGISTRATE_FIRSTMATE_ROOT=$PRODUCER_ROOT`, and `MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true`; invoke the standing owner-authorized guarded deployment only after the PR is green. This worker does not merge or deploy. Do not paste session/provider credentials into a log. With shell tracing still disabled, use an environment variable for the owner token:

```bash
export GATEWAY_ORIGIN='https://<owner-gateway-host>'
read -rsp 'Owner Magistrate session token: ' MAGISTRATE_OWNER_SESSION_TOKEN; echo
export MAGISTRATE_OWNER_SESSION_TOKEN
curl --fail --silent --show-error \
  -H "Authorization: Bearer $MAGISTRATE_OWNER_SESSION_TOKEN" \
  "$GATEWAY_ORIGIN/api/v1/health" > soak-p0-001-health.json
curl --fail --silent --show-error \
  -H "Authorization: Bearer $MAGISTRATE_OWNER_SESSION_TOKEN" \
  "$GATEWAY_ORIGIN/api/v1/diagnostics/soak" > soak-p0-001-before.json
python3 - <<'PY'
import json
for name in ('soak-p0-001-health.json', 'soak-p0-001-before.json'):
    data = json.load(open(name, encoding='utf-8'))
    producer = data['firstmate_producer']
    assert producer['expected_commit'] == '2af0d17014cb2e244aa441bfe6df16c4f630475b'
    assert producer['required'] is True and producer['status'] == 'ready'
print('producer readiness: PASS')
PY
```

## 3. Build the exact candidate for a physical iPhone

On the signing Mac, from that detached candidate worktree:

```bash
cd frontend
npm ci
npx expo-doctor
npm run typecheck
npm run lint
npm test
npx expo export -p web
export EXPO_PUBLIC_GATEWAY_URL="$GATEWAY_ORIGIN/api/v1"
read -rp 'Physical iPhone UDID: ' PHYSICAL_IPHONE_UDID
npx expo run:ios --device "$PHYSICAL_IPHONE_UDID" --configuration Release \
  2>&1 | tee "../soak-p0-001-ios-build-${CANDIDATE_SHA}.log"
cd ..
printf 'candidate=%s device_udid_suffix=%s build_log_sha256=%s\n' \
  "$CANDIDATE_SHA" "${PHYSICAL_IPHONE_UDID: -6}" \
  "$(shasum -a 256 "soak-p0-001-ios-build-${CANDIDATE_SHA}.log" | awk '{print $1}')" \
  | tee soak-p0-001-build.txt
```

The owner must confirm Xcode installed this Release build on a **physical** iPhone and record iOS version and app build/version. A simulator or browser is not a substitute.

## 4. Execute twelve harmless live semantic operations

Create one disposable Firstmate task, scoped to read-only inspection of the candidate repository. It must make no commit, push, provider call, deployment, or paid inference call beyond already-authorized subscription use. Keep the iPhone signed in as the same owner principal.

Drive twelve separate persisted Pi assistant turns (not twelve terminal lines) and require visible inert prose markers exactly `SOAK-P0-001 operation 01` through `SOAK-P0-001 operation 12`. Compare each marker against canonical `/api/v1/activity`, never terminal snapshots.

| Operations | Physical-device state | Required observation |
|---|---|---|
| 01–03 | App foreground | Rows arrive once, in order; prose is readable |
| 04–06 | App backgrounded for at least 60 seconds | Existing configured push/background signal arrives; reopening catches up without duplicates |
| 07–09 | App force-quit before publication | After relaunch, replay catches up in order without loss/duplicates |
| 10 | Network disabled during publication, then restored | Reconnect catches up from the prior cursor |
| 11 | App foreground | Put the disposable task under a real keyed captain hold; the exact decision appears once; approve/reject it on iPhone and verify the same key resolves |
| 12 | App background then foreground | Complete the disposable task through structured Firstmate state; objective revises to completed without treating an earlier `worker.final` as task completion |

Use Firstmate's normal task/session interfaces for the turns. For operation 11, the owner may use the pinned guarded hold command on the disposable task (replace the safe task id):

```bash
FM_HOME="$DEPLOYED_FM_HOME" FM_ROOT_OVERRIDE="$PRODUCER_ROOT" \
  "$PRODUCER_ROOT/bin/fm-captain-hold.sh" hold soak-p0-001-runtime \
  --title 'SOAK P0 physical decision' \
  --reason 'Choose approve or reject for disposable SOAK-P0-001 evidence only.'
```

Do not use a synthetic decision payload or edit journal/task files. Resolve it only through Magistrate's existing confirmed Attention action. Complete only the disposable task through normal Firstmate structured mechanics.

## 5. Capture bounded comparison and verdict

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $MAGISTRATE_OWNER_SESSION_TOKEN" \
  "$GATEWAY_ORIGIN/api/v1/activity/snapshot" > soak-p0-001-activity.json
curl --fail --silent --show-error \
  -H "Authorization: Bearer $MAGISTRATE_OWNER_SESSION_TOKEN" \
  "$GATEWAY_ORIGIN/api/v1/diagnostics/soak" > soak-p0-001-after.json
unset MAGISTRATE_OWNER_SESSION_TOKEN
```

Record only redacted canonical ids/cursors and these counts; do not archive prompts, terminal bytes, tokens, provider data, or filesystem paths:

```text
candidate_sha=<40 hex>
producer_commit=2af0d17014cb2e244aa441bfe6df16c4f630475b
physical_device=true
ios_version=<observed>
app_version/build=<observed>
operations_expected=12
operations_seen_api=<count>
operations_seen_device=<count>
duplicate_ids=0
missing_ids=0
background_catch_up=PASS|FAIL
force_quit_catch_up=PASS|FAIL
network_reconnect=PASS|FAIL
decision_key_match=PASS|FAIL
completion_revision=PASS|FAIL
verdict=PASS|FAIL
```

Any mismatch, fallback to terminal-derived captain chat, missing/duplicate event, stale decision key, absent completion revision, non-ready producer, or candidate change is a **FAIL/NO-GO**, not a partial pass.

## 6. Roll back after a failed or completed test

First disable required mode/remove the code-root selection and restart Gateway. Quiesce/restart Pi sessions onto the prior Firstmate root. Then:

```bash
scripts/install_firstmate_producer.sh deactivate \
  --root "$PRODUCER_ROOT" --fm-home "$DEPLOYED_FM_HOME"
```

Retain `state/captain-events/` until the owner has completed audit/replay; deactivation intentionally does not delete it.
