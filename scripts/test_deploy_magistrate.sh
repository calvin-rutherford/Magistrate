#!/usr/bin/env bash
set -euo pipefail

# Contract test for the guarded updater. It uses throwaway repositories and
# command stubs, so it never contacts the demo host or touches a real checkout.
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
REMOTE="$ROOT/remote.git"
DEPLOY="$ROOT/deploy"
SEED="$ROOT/seed"
STUBS="$ROOT/stubs"
mkdir -p "$STUBS" "$ROOT/state"
git init --bare "$REMOTE" >/dev/null
git init -b main "$SEED" >/dev/null
git -C "$SEED" config user.email test@example.invalid
git -C "$SEED" config user.name deployment-test
mkdir -p "$SEED/frontend" "$SEED/gateway" "$SEED/scripts"
cp scripts/smoke_magistrate.sh "$SEED/scripts/smoke_magistrate.sh"
chmod +x "$SEED/scripts/smoke_magistrate.sh"
cat > "$SEED/gateway/.env" <<EOF
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=$ROOT/state/magistrate.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=test-bootstrap
MAGISTRATE_SECRET_KEY=test-secret
MAGISTRATE_CORS_ORIGINS=https://demo.example.invalid
EOF
printf '{}\n' > "$SEED/frontend/package-lock.json"
printf '{}\n' > "$SEED/frontend/package.json"
printf 'dist/\n' > "$SEED/frontend/.gitignore"
git -C "$SEED" add .
git -C "$SEED" commit -m initial >/dev/null
git -C "$SEED" remote add origin "$REMOTE"
git -C "$SEED" push -u origin main >/dev/null
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main
git clone -q "$REMOTE" "$DEPLOY"

cat > "$STUBS/npm" <<'EOF'
#!/usr/bin/env bash
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
[[ "${2:-}" != is-active ]] || exit 0
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

run_update() {
  PATH="$STUBS:$PATH" MAGISTRATE_DEPLOY_DIR="$DEPLOY" MAGISTRATE_DEPLOY_LOCK="$ROOT/deploy.lock" \
    MAGISTRATE_READINESS_TIMEOUT_SECONDS="${TEST_READINESS_TIMEOUT_SECONDS:-3}" \
    MAGISTRATE_READINESS_INTERVAL_SECONDS=0 MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS=1 \
    MAGISTRATE_TRUSTED_SMOKE="${TEST_TRUSTED_SMOKE:-0}" MAGISTRATE_TEST_CURL_COUNT="$ROOT/curl.count" \
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
