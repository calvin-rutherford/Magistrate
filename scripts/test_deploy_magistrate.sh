#!/usr/bin/env bash
set -euo pipefail

# Contract test for the guarded updater. It uses throwaway repositories and
# command stubs, so it never contacts the demo host or touches a real checkout.
ROOT="$(mktemp -d)"
PI_SERVER_PID=""
cleanup() {
  if [[ -n "$PI_SERVER_PID" ]]; then kill "$PI_SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$ROOT"
}
trap cleanup EXIT
REMOTE="$ROOT/remote.git"
DEPLOY="$ROOT/deploy"
SEED="$ROOT/seed"
STUBS="$ROOT/stubs"
mkdir -p "$STUBS" "$ROOT/state"
git init --bare "$REMOTE" >/dev/null
git init -b main "$SEED" >/dev/null
git -C "$SEED" config user.email test@example.invalid
git -C "$SEED" config user.name deployment-test
mkdir -p "$SEED/frontend" "$SEED/gateway/app" "$SEED/pi-extension" "$SEED/scripts"
cp gateway/app/pi_adapter_ipc.py "$SEED/gateway/app/pi_adapter_ipc.py"
printf '' > "$SEED/gateway/app/__init__.py"
cp scripts/smoke_magistrate.sh "$SEED/scripts/smoke_magistrate.sh"
cat > "$SEED/scripts/install_firstmate_producer.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${MAGISTRATE_TEST_INSTALL_LOG:?}"
EOF
chmod +x "$SEED/scripts/smoke_magistrate.sh" "$SEED/scripts/install_firstmate_producer.sh"
cat > "$SEED/gateway/.env" <<EOF
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=$ROOT/state/magistrate.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=test-bootstrap
MAGISTRATE_SECRET_KEY=test-secret
MAGISTRATE_CORS_ORIGINS=https://demo.example.invalid
MAGISTRATE_PI_OWNERSHIP_ENABLED=false
EOF
printf '{}\n' > "$SEED/frontend/package-lock.json"
printf '{}\n' > "$SEED/frontend/package.json"
printf '{}\n' > "$SEED/pi-extension/package-lock.json"
printf '{}\n' > "$SEED/pi-extension/package.json"
printf 'dist/\n' > "$SEED/frontend/.gitignore"
git -C "$SEED" add .
git -C "$SEED" commit -m initial >/dev/null
git -C "$SEED" remote add origin "$REMOTE"
git -C "$SEED" push -u origin main >/dev/null
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main
git clone -q "$REMOTE" "$DEPLOY"
chmod 600 "$DEPLOY/gateway/.env"

cat > "$STUBS/npm" <<'EOF'
#!/usr/bin/env bash
printf '%s|%s\n' "$PWD" "$*" >> "${MAGISTRATE_TEST_NPM_LOG:?}"
exit 0
EOF
cat > "$STUBS/npx" <<'EOF'
#!/usr/bin/env bash
mkdir -p dist
printf '<!DOCTYPE html>Magistrate\n' > dist/index.html
printf '<!DOCTYPE html>Magistrate chat\n' > dist/chat.html
printf '<!DOCTYPE html>Magistrate voice\n' > dist/voice.html
EOF
cat > "$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${MAGISTRATE_TEST_SYSTEMCTL_LOG:?}"
if [[ "${2:-}" == is-active ]]; then
  printf 'active\n'
elif [[ "${2:-}" == show ]]; then
  printf '%s\n' "${MAGISTRATE_TEST_PI_SERVER_PID:?}"
fi
exit 0
EOF
cat > "$STUBS/pi" <<'EOF'
#!/usr/bin/env bash
printf '%s|%s\n' "$PWD" "$*" >> "${MAGISTRATE_TEST_PI_LOG:?}"
EOF
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
count_file="${MAGISTRATE_TEST_CURL_COUNT:?}"
count="$(cat "$count_file" 2>/dev/null || printf '0')"
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if (( count == 1 )); then
  # Model the systemd restart window: curl sees connection refused first.
  printf '000'
else
  printf '401'
fi
EOF
chmod +x "$STUBS"/*
printf '0\n' > "$ROOT/curl.count"
: > "$ROOT/npm.log"
: > "$ROOT/systemctl.log"
: > "$ROOT/pi.log"
# The enabled rollout test backs up a real minimal SQLite ownership ledger.
python3 - "$ROOT/state/magistrate.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute(
        'CREATE TABLE pi_semantic_dispatches ('
        'state TEXT NOT NULL, adapter_acknowledged_at INTEGER)'
    )
PY
chmod 600 "$ROOT/state/magistrate.sqlite3"

run_update() {
  PATH="$STUBS:$PATH" MAGISTRATE_DEPLOY_DIR="$DEPLOY" MAGISTRATE_DEPLOY_LOCK="$ROOT/deploy.lock" \
    MAGISTRATE_READINESS_TIMEOUT_SECONDS="${TEST_READINESS_TIMEOUT_SECONDS:-3}" \
    MAGISTRATE_READINESS_INTERVAL_SECONDS=0 MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS=1 \
    MAGISTRATE_TRUSTED_SMOKE="${TEST_TRUSTED_SMOKE:-0}" MAGISTRATE_TEST_CURL_COUNT="$ROOT/curl.count" \
    MAGISTRATE_TEST_INSTALL_LOG="$ROOT/producer-install.log" \
    MAGISTRATE_TEST_NPM_LOG="$ROOT/npm.log" \
    MAGISTRATE_TEST_SYSTEMCTL_LOG="$ROOT/systemctl.log" \
    MAGISTRATE_TEST_PI_LOG="$ROOT/pi.log" \
    MAGISTRATE_TEST_PI_SERVER_PID="${TEST_PI_MAIN_PID:-${PI_SERVER_PID:-1}}" \
    MAGISTRATE_BACKUP_DIR="${TEST_BACKUP_DIR:-}" \
    bash scripts/deploy_magistrate.sh
}

# The first probe is an immediate connection refusal (HTTP 000), but the
# recovered service is accepted on the next bounded probe.
DELAYED_OUTPUT="$(run_update 2>&1)"
test "$(cat "$ROOT/curl.count")" -ge 2
grep -Fq 'gateway readiness verified' <<<"$DELAYED_OUTPUT"
test "$(git -C "$DEPLOY" rev-parse HEAD)" = "$(git -C "$REMOTE" rev-parse refs/heads/main)"

# A connection refusal must be retried, then fail with bounded diagnostics if
# the service never recovers. The synthetic secret must not enter the output.
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf '000'
EOF
chmod +x "$STUBS/curl"
if IMMEDIATE_OUTPUT="$(TEST_READINESS_TIMEOUT_SECONDS=1 run_update 2>&1)"; then
  echo 'immediate connection refusal was not rejected' >&2
  exit 1
fi
grep -Fq 'gateway readiness timed out after 1s' <<<"$IMMEDIATE_OUTPUT"
grep -Fq 'last HTTP response 000' <<<"$IMMEDIATE_OUTPUT"
if grep -Fq 'test-bootstrap' <<<"$IMMEDIATE_OUTPUT"; then
  echo 'timeout diagnostics exposed the bootstrap secret' >&2
  exit 1
fi

# A reachable but not-ready response is also retried and times out rather than
# being mistaken for process health.
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf '503'
EOF
chmod +x "$STUBS/curl"
if TIMEOUT_OUTPUT="$(TEST_READINESS_TIMEOUT_SECONDS=1 run_update 2>&1)"; then
  echo 'unready gateway was not rejected' >&2
  exit 1
fi
grep -Fq 'last HTTP response 503' <<<"$TIMEOUT_OUTPUT"

# Malformed production CORS configuration is rejected before build/restart.
git -C "$DEPLOY" config user.email test@example.invalid
git -C "$DEPLOY" config user.name deployment-test
sed -i 's#MAGISTRATE_CORS_ORIGINS=.*#MAGISTRATE_CORS_ORIGINS=*#' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m invalid-cors >/dev/null
if run_update >/dev/null 2>&1; then
  echo 'wildcard CORS configuration was not rejected' >&2
  exit 1
fi
sed -i 's#MAGISTRATE_CORS_ORIGINS=.*#MAGISTRATE_CORS_ORIGINS=https://demo.example.invalid#' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m restore-cors >/dev/null

# The new producer gate is opt-in. Once required, a missing immutable code root
# must refuse before a build/restart rather than silently using FM_HOME's older
# unmanaged checkout.
printf 'MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true\nFM_HOME=%s\n' "$ROOT/firstmate-home" >> "$DEPLOY/gateway/.env"
mkdir -p "$ROOT/firstmate-home"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m require-missing-producer >/dev/null
if MISSING_PRODUCER_OUTPUT="$(run_update 2>&1)"; then
  echo 'required missing Firstmate producer was not rejected' >&2
  exit 1
fi
grep -Fq 'required captain producer needs absolute FM_HOME and MAGISTRATE_FIRSTMATE_ROOT' <<<"$MISSING_PRODUCER_OUTPUT"

# With both roots configured, required mode must run immutable verification and
# activated-state readiness before proceeding with the ordinary deploy.
printf 'MAGISTRATE_FIRSTMATE_ROOT=%s\n' "$ROOT/managed-firstmate-code" >> "$DEPLOY/gateway/.env"
mkdir -p "$ROOT/managed-firstmate-code"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m require-configured-producer >/dev/null
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf '401'
EOF
chmod +x "$STUBS/curl"
: > "$ROOT/producer-install.log"
CONFIGURED_PRODUCER_OUTPUT="$(run_update 2>&1)"
grep -Fxq "verify --root $ROOT/managed-firstmate-code" "$ROOT/producer-install.log"
grep -Fxq "ready --root $ROOT/managed-firstmate-code --fm-home $ROOT/firstmate-home" "$ROOT/producer-install.log"
grep -Fq 'gateway readiness verified' <<<"$CONFIGURED_PRODUCER_OUTPUT"
sed -i 's/MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true/MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=false/' "$DEPLOY/gateway/.env"
sed -i '/^MAGISTRATE_FIRSTMATE_ROOT=/d' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m restore-optional-producer >/dev/null

# Restore the normal stub and exercise the trusted-host authenticated smoke
# through the deploy script, while keeping its credentials out of output.
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *'/auth/session'* ]]; then
  printf '{"session_token":"smoke-only-token","expires_at":4102444800}'
else
  printf '401'
fi
EOF
chmod +x "$STUBS/curl"
TRUSTED_OUTPUT="$(TEST_TRUSTED_SMOKE=1 run_update 2>&1)"
grep -Fq 'authenticated deployment smoke passed' <<<"$TRUSTED_OUTPUT"
if grep -Fq 'test-bootstrap' <<<"$TRUSTED_OUTPUT" || grep -Fq 'smoke-only-token' <<<"$TRUSTED_OUTPUT"; then
  echo 'trusted deployment output exposed a credential' >&2
  exit 1
fi

echo dirty > "$DEPLOY/unique-change"
if run_update >/dev/null 2>&1; then
  echo 'dirty checkout was not rejected' >&2
  exit 1
fi
test -f "$DEPLOY/unique-change"
rm "$DEPLOY/unique-change"

# A checkout-local database must be rejected so deploys cannot strand state.
git -C "$DEPLOY" config user.email test@example.invalid
git -C "$DEPLOY" config user.name deployment-test
sed -i "s#MAGISTRATE_DB_PATH=$ROOT/state/magistrate.sqlite3#MAGISTRATE_DB_PATH=$DEPLOY/gateway/magistrate.sqlite3#" "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m invalid-db-path >/dev/null
if run_update >/dev/null 2>&1; then
  echo 'checkout-local database path was not rejected' >&2
  exit 1
fi
sed -i "s#MAGISTRATE_DB_PATH=$DEPLOY/gateway/magistrate.sqlite3#MAGISTRATE_DB_PATH=$ROOT/state/magistrate.sqlite3#" "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m restore-db-path >/dev/null

# Unset now means enabled, so a production env without the complete Pi shape
# must fail before build or service lifecycle rather than silently use Herdr.
sed -i '/^MAGISTRATE_PI_OWNERSHIP_ENABLED=/d' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m default-pi-enabled >/dev/null
if DEFAULT_PI_OUTPUT="$(run_update 2>&1)"; then
  echo 'default-on Pi ownership accepted an incomplete production environment' >&2
  exit 1
fi
grep -Fq 'enabled Pi ownership requires MAGISTRATE_PI_RUNTIME_DIR' <<<"$DEFAULT_PI_OUTPUT"
printf 'MAGISTRATE_PI_OWNERSHIP_ENABLED=maybe\n' >> "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m malformed-pi-flag >/dev/null
if MALFORMED_PI_OUTPUT="$(run_update 2>&1)"; then
  echo 'malformed Pi ownership flag was not rejected' >&2
  exit 1
fi
grep -Fq 'must be a supported boolean literal' <<<"$MALFORMED_PI_OUTPUT"
sed -i 's/MAGISTRATE_PI_OWNERSHIP_ENABLED=maybe/MAGISTRATE_PI_OWNERSHIP_ENABLED=false/' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m restore-pi-compatibility >/dev/null
printf 'MAGISTRATE_PI_OWNERSHIP_ENABLED=true\n' >> "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m duplicate-pi-flag >/dev/null
if DUPLICATE_PI_OUTPUT="$(run_update 2>&1)"; then
  echo 'duplicate Pi ownership flag was not rejected' >&2
  exit 1
fi
grep -Fq 'duplicate environment assignments are ambiguous' <<<"$DUPLICATE_PI_OUTPUT"
sed -i '$d' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m remove-duplicate-pi-flag >/dev/null

# A complete enabled rollout creates a verified backup, tests/installs the
# extension, restarts only Gateway plus the dedicated Pi unit, and proves a
# same-UID mode-0600 listening socket before reporting bounded state counts.
PI_RUNTIME="$ROOT/pi-runtime"
mkdir -m 700 "$PI_RUNTIME"
printf '%s\n' "$(printf 'P%.0s' {1..48})" > "$PI_RUNTIME/pi-ownership.key"
chmod 600 "$PI_RUNTIME/pi-ownership.key"
PI_SOCKET="$PI_RUNTIME/pi-ownership.sock"
python3 - "$PI_SOCKET" <<'PY' &
import hashlib, hmac, json, os, socket, sys
path = sys.argv[1]
key = b'P' * 48
def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
server = socket.socket(socket.AF_UNIX)
server.bind(path)
os.chmod(path, 0o600)
server.listen()
while True:
    connection, _ = server.accept()
    with connection:
        raw = b''
        while not raw.endswith(b'\n'):
            chunk = connection.recv(4096)
            if not chunk:
                break
            raw += chunk
        try:
            frame = json.loads(raw)
            body = frame['body']
            expected = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
            if (not hmac.compare_digest(frame['mac'], expected)
                    or body.get('message_type') != 'probe'):
                continue
            response = {
                'schema_version': 'magistrate.pi.ipc.v1',
                'event_type': 'ready',
                'request_nonce': body['request_nonce'],
            }
            mac = hmac.new(key, canonical(response), hashlib.sha256).hexdigest()
            connection.sendall(canonical({'body': response, 'mac': mac}) + b'\n')
        except (KeyError, ValueError, TypeError):
            pass
PY
PI_SERVER_PID=$!
for _ in {1..50}; do [[ -S "$PI_SOCKET" ]] && break; sleep 0.02; done
cat >> "$DEPLOY/gateway/.env" <<EOF
MAGISTRATE_PI_OWNERSHIP_ENABLED=true
MAGISTRATE_PI_RUNTIME_DIR=$PI_RUNTIME
MAGISTRATE_PI_IPC_KEY_PATH=$PI_RUNTIME/pi-ownership.key
MAGISTRATE_PI_ADAPTER_SOCKET=$PI_SOCKET
MAGISTRATE_PI_ADAPTER_JOURNAL=$PI_RUNTIME/pi-ownership.journal
MAGISTRATE_PI_ADAPTER_UID=$(id -u)
MAGISTRATE_PI_CAPABILITY_TTL_SECONDS=120
MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS=1
MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS=20
MAGISTRATE_PI_RECOVERY_SECONDS=3
EOF
sed -i '/MAGISTRATE_PI_OWNERSHIP_ENABLED=false/d' "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m enable-pi-ownership >/dev/null
: > "$ROOT/npm.log"; : > "$ROOT/systemctl.log"; : > "$ROOT/pi.log"
ENABLED_PI_OUTPUT="$(run_update 2>&1)"
grep -Fq 'Pi ownership backup verified for deployed commit' <<<"$ENABLED_PI_OUTPUT"
grep -Fq 'ownership extension; trusted socket is listening' <<<"$ENABLED_PI_OUTPUT"
grep -Fq 'prepared=0 bound=0 finalized=0 failed=0 recovery_backlog=0' <<<"$ENABLED_PI_OUTPUT"
grep -Fxq "$DEPLOY/pi-extension|install $DEPLOY/pi-extension" "$ROOT/pi.log"
grep -Fxq -- '--user restart magistrate-gateway.service' "$ROOT/systemctl.log"
grep -Fxq -- '--user restart magistrate-captain-pi.service' "$ROOT/systemctl.log"
PI_RESTART_LINE="$(grep -nFx -- '--user restart magistrate-captain-pi.service' "$ROOT/systemctl.log" | cut -d: -f1)"
GATEWAY_RESTART_LINE="$(grep -nFx -- '--user restart magistrate-gateway.service' "$ROOT/systemctl.log" | cut -d: -f1)"
test "$PI_RESTART_LINE" -lt "$GATEWAY_RESTART_LINE"
test "$(find "$ROOT/state/backups" -type f -name '*.sqlite3' | wc -l)" -eq 1
test "$(find "$ROOT/state/backups" -type f -name '*.commit' | wc -l)" -eq 1
BACKED_COMMIT="$(cat "$(find "$ROOT/state/backups" -type f -name '*.commit')")"
test "$BACKED_COMMIT" = "$(git -C "$DEPLOY" rev-parse HEAD)"

# A same-UID signed socket owned by any process other than the restarted unit's
# exact MainPID is not activation proof and must not permit a Gateway restart.
: > "$ROOT/systemctl.log"
if PID_MISMATCH_OUTPUT="$(TEST_PI_MAIN_PID=$((PI_SERVER_PID + 1)) \
    TEST_BACKUP_DIR="$ROOT/state/backups-pid-mismatch" run_update 2>&1)"; then
  echo 'adapter peer outside the dedicated service MainPID was not rejected' >&2
  exit 1
fi
grep -Fq 'adapter peer is not the dedicated service MainPID' <<<"$PID_MISMATCH_OUTPUT"
if grep -Fxq -- '--user restart magistrate-gateway.service' "$ROOT/systemctl.log"; then
  echo 'Gateway restarted after Pi MainPID proof failed' >&2
  exit 1
fi

kill "$PI_SERVER_PID" 2>/dev/null || true
wait "$PI_SERVER_PID" 2>/dev/null || true
PI_SERVER_PID=""
sed -i '/^MAGISTRATE_PI_/d' "$DEPLOY/gateway/.env"
printf 'MAGISTRATE_PI_OWNERSHIP_ENABLED=false\n' >> "$DEPLOY/gateway/.env"
git -C "$DEPLOY" add gateway/.env
git -C "$DEPLOY" commit -m restore-pi-compatibility-after-rollout-test >/dev/null

# A local-only commit must survive a remote update that would otherwise diverge.
echo local > "$DEPLOY/local-only"
git -C "$DEPLOY" add local-only
git -C "$DEPLOY" commit -m local-only >/dev/null
LOCAL_HEAD="$(git -C "$DEPLOY" rev-parse HEAD)"
echo remote > "$SEED/remote-only"
git -C "$SEED" add remote-only
git -C "$SEED" commit -m remote-only >/dev/null
git -C "$SEED" push origin main >/dev/null
if run_update >/dev/null 2>&1; then
  echo 'divergent checkout was not rejected' >&2
  exit 1
fi
test "$(git -C "$DEPLOY" rev-parse HEAD)" = "$LOCAL_HEAD"
test -f "$DEPLOY/local-only"

# The trusted smoke consumes the deployment env file but must not expose either
# the bootstrap secret or the issued bearer in its output.
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *'/auth/session'* ]]; then
  printf '{"session_token":"smoke-only-token","expires_at":4102444800}'
fi
EOF
chmod +x "$STUBS/curl"
SMOKE_OUTPUT="$(PATH="$STUBS:$PATH" MAGISTRATE_ENV_FILE="$DEPLOY/gateway/.env" MAGISTRATE_HEALTH_URL='http://127.0.0.1:8000/api/v1/health' bash scripts/smoke_magistrate.sh)"
test "$SMOKE_OUTPUT" = 'authenticated deployment smoke passed (health and /agents)'
if grep -Fq 'test-bootstrap' <<<"$SMOKE_OUTPUT" || grep -Fq 'smoke-only-token' <<<"$SMOKE_OUTPUT"; then
  echo 'smoke output exposed a credential' >&2
  exit 1
fi

echo 'deployment safeguards passed'
