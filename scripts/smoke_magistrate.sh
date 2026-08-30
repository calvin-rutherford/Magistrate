#!/usr/bin/env bash
set -euo pipefail
set +x 2>/dev/null || true

# Trusted-host smoke check. It reads the bootstrap secret from the deployment
# env file and never prints the secret or an issued bearer. Do not call this
# from an untrusted CI job; the unauthenticated reachability check belongs in
# the deployment workflow instead.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
ENV_FILE="${MAGISTRATE_ENV_FILE:-$DEPLOY_DIR/gateway/.env}"
HEALTH_URL="${MAGISTRATE_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"
APPLICATION_PATH="${MAGISTRATE_SMOKE_APPLICATION_PATH:-/agents}"

fail() { echo "authenticated deployment smoke failed: $1" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || fail "missing environment file"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

read_env_value() {
  local key="$1" value
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[[:space:]]+/, "", $2); print $2; exit }' "$ENV_FILE")"
  value="${value%\"}"; value="${value#\"}"
  printf '%s' "$value"
}

bootstrap_secret="$(read_env_value MAGISTRATE_BOOTSTRAP_SECRET)"
[[ -n "$bootstrap_secret" ]] || fail "bootstrap secret is not configured"
api_root="${HEALTH_URL%/health}"
[[ "$api_root" == */api/v1 ]] || fail "health URL must end in /api/v1/health"

# Keep credentials in pipes/variables rather than command-line arguments. The
# response is captured only long enough to extract the opaque bearer locally.
request_body="$(printf '%s' "$bootstrap_secret" | python3 -c 'import json, sys; print(json.dumps({"bootstrap_secret": sys.stdin.read()}))')"
session_json="$(curl --silent --show-error --fail --connect-timeout 5 --max-time 15 \
  -H 'Content-Type: application/json' --data-binary "$request_body" \
  "$api_root/auth/session" 2>/dev/null)" || fail "session issuance"
unset request_body bootstrap_secret

session_token="$(printf '%s' "$session_json" | python3 -c 'import json, sys; value=json.load(sys.stdin); token=value.get("session_token"); expires=value.get("expires_at"); assert isinstance(token, str) and token and isinstance(expires, int) and expires > 0; print(token)' 2>/dev/null)" || fail "invalid session response"
unset session_json

curl --silent --fail --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $session_token" "$api_root/auth/session" >/dev/null 2>&1 \
  || fail "authenticated session validation"
curl --silent --fail --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $session_token" "$HEALTH_URL" >/dev/null 2>&1 \
  || fail "authenticated health"
curl --silent --fail --connect-timeout 5 --max-time 15 \
  -H "Authorization: Bearer $session_token" "$api_root$APPLICATION_PATH" >/dev/null 2>&1 \
  || fail "authenticated application endpoint"
unset session_token

echo "authenticated deployment smoke passed (health and ${APPLICATION_PATH})"
