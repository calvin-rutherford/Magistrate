#!/usr/bin/env bash
set -euo pipefail
set +x 2>/dev/null || true

# External Friend Beta smoke. This is intentionally run from a trusted shell
# with a dedicated test grant before device enrollment. It proves HTTPS,
# principal/session validation, native transcript read, WSS first-frame
# authentication, and smoke-grant revocation without sending a prompt or
# making a provider call.
GATEWAY_URL="${MAGISTRATE_FRIEND_BETA_GATEWAY_URL:-}"
ACCESS_FILE="${MAGISTRATE_FRIEND_BETA_ACCESS_FILE:-}"
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
PYTHON="${MAGISTRATE_SMOKE_PYTHON:-$DEPLOY_DIR/gateway/.venv/bin/python}"

fail() { echo "Friend Beta external smoke failed: $1" >&2; exit 1; }
[[ -n "$GATEWAY_URL" ]] || fail "MAGISTRATE_FRIEND_BETA_GATEWAY_URL is required"
[[ -n "$ACCESS_FILE" ]] || fail "MAGISTRATE_FRIEND_BETA_ACCESS_FILE is required"
[[ -f "$ACCESS_FILE" && ! -L "$ACCESS_FILE" ]] || fail "access file must be a regular non-symlink file"
[[ "$(stat -c '%u' "$ACCESS_FILE")" == "$(id -u)" && "$(stat -c '%a' "$ACCESS_FILE")" == 600 ]] \
  || fail "access file must be owned by the current user with mode 0600"
[[ -x "$PYTHON" ]] || fail "set MAGISTRATE_SMOKE_PYTHON to the Gateway virtualenv Python"
command -v curl >/dev/null 2>&1 || fail "curl is required"

url_evidence="$($PYTHON - "$GATEWAY_URL" <<'PY'
import json, sys
from urllib.parse import urlsplit
value = sys.argv[1]
parsed = urlsplit(value)
if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or not parsed.path.endswith('/api/v1')
        or parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
    raise SystemExit(1)
print(json.dumps({
    'api_root': value.rstrip('/'),
    'ws_root': 'wss://' + parsed.netloc + parsed.path.rstrip('/') + '/events',
}, separators=(',', ':')))
PY
)" || fail "Gateway URL must be a non-local HTTPS /api/v1 endpoint without credentials/query/fragment"
api_root="$(printf '%s' "$url_evidence" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["api_root"])')"
ws_url="$(printf '%s' "$url_evidence" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["ws_root"])')"
unset url_evidence

expected_user="$($PYTHON - "$ACCESS_FILE" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding='utf-8') as source:
    payload = json.load(source)
value = payload.get('user_id')
if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value):
    raise SystemExit(1)
print(value)
PY
)" || fail "access file does not contain a valid expected principal"
access_code="$($PYTHON - "$ACCESS_FILE" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding='utf-8') as source:
    value = json.load(source).get('access_code')
if not isinstance(value, str) or not re.fullmatch(r'mgb_[A-Za-z0-9_-]{32,64}', value):
    raise SystemExit(1)
print(value)
PY
)" || fail "access file does not contain a valid issued code"
request_body="$(printf '%s' "$access_code" | "$PYTHON" -c 'import json,sys; print(json.dumps({"access_code":sys.stdin.read()}))')"
unset access_code

session_json="$(printf '%s' "$request_body" | \
  curl --silent --show-error --fail --connect-timeout 5 --max-time 20 \
    -H 'Content-Type: application/json' --data-binary @- \
    "$api_root/auth/friend-beta/session" 2>/dev/null)" || fail "Friend Beta session exchange over HTTPS"
unset request_body
session_token="$(printf '%s' "$session_json" | "$PYTHON" -c '
import json,re,sys
value=json.load(sys.stdin)
token=value.get("session_token")
assert isinstance(token,str) and re.fullmatch(r"[A-Za-z0-9_-]{32,64}",token)
assert value.get("auth_method")=="friend-beta-access"
assert value.get("user_id")==sys.argv[1]
assert isinstance(value.get("expires_at"),int) and isinstance(value.get("renewable_until"),int)
assert "access_code" not in value
print(token)
' "$expected_user" 2>/dev/null)" || fail "invalid, misattributed, or secret-reflecting Friend Beta session response"
unset session_json expected_user

# Feed the sensitive header through curl's stdin config so neither credential
# appears in process arguments. Tokens have already been restricted to a safe
# alphabet before interpolation into the config line.
authenticated_request() {
  local method="$1"
  local url="$2"
  printf 'header = "Authorization: Bearer %s"\n' "$session_token" | \
    curl --config - --silent --show-error --fail --connect-timeout 5 --max-time 20 \
      --request "$method" "$url" >/dev/null 2>&1
}
authenticated_request GET "$api_root/auth/session" \
  || fail "authenticated session validation"
authenticated_request GET "$api_root/health" \
  || fail "authenticated health"
authenticated_request GET "$api_root/magi/conversations/current?limit=1" \
  || fail "principal-owned native conversation read"

# Token travels over stdin, never in the URL or process arguments. The script
# expects the same websockets dependency as the Gateway runtime.
printf '%s' "$session_token" | "$PYTHON" -c '
import asyncio,json,sys
import websockets
url=sys.argv[1]
token=sys.stdin.read()
async def check():
    async with websockets.connect(url, open_timeout=10, close_timeout=5, max_size=65536) as socket:
        await socket.send(json.dumps({"type":"auth","token":token,"target":"captain","chat_mode":"native"}))
        reply=json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if reply != {"type":"connected","target":"captain"}:
            raise RuntimeError("unexpected WebSocket acknowledgement")
asyncio.run(check())
' "$ws_url" >/dev/null 2>&1 || fail "authenticated WSS first-frame handshake"

# A smoke credential must not remain usable after evidence collection.
authenticated_request POST "$api_root/auth/session/revoke" \
  || fail "smoke grant revocation"
unset session_token

echo "Friend Beta external smoke passed (HTTPS session/health/native read, authenticated WSS, and smoke-grant revocation)"
