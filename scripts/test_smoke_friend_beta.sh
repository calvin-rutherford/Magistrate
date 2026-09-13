#!/usr/bin/env bash
set -euo pipefail

ROOT="$(mktemp -d)"
cleanup() { rm -rf "$ROOT"; }
trap cleanup EXIT
mkdir -p "$ROOT/bin" "$ROOT/python/websockets"

cat > "$ROOT/python/websockets/__init__.py" <<'PY'
import json
class _Socket:
    async def send(self, value):
        payload = json.loads(value)
        assert payload['type'] == 'auth'
        assert payload['token'] == 'smoke-session-token-0123456789ABCDEF'
        assert payload['chat_mode'] == 'native'
    async def recv(self):
        return json.dumps({'type': 'connected', 'target': 'captain'})
class _Connection:
    async def __aenter__(self): return _Socket()
    async def __aexit__(self, *args): return False
def connect(*args, **kwargs):
    assert args[0] == 'wss://beta.example.test/api/v1/events'
    return _Connection()
PY

cat > "$ROOT/bin/curl" <<'SH'
#!/usr/bin/env bash
url="${!#}"
printf '%s\n' "$url" >> "${FRIEND_BETA_TEST_CURL_LOG:?}"
printf '%s\n' "$@" >> "${FRIEND_BETA_TEST_CURL_ARGS_LOG:?}"
if [[ "$url" == */auth/friend-beta/session ]]; then
  printf '%s\n' '{"session_token":"smoke-session-token-0123456789ABCDEF","token_type":"Bearer","expires_at":4102444800,"renewable_until":4102444800,"scopes":["account","notifications","read"],"user_id":"smoke-friend","auth_method":"friend-beta-access","onboarding_required":true}'
fi
SH
chmod +x "$ROOT/bin/curl"

cat > "$ROOT/access.json" <<'JSON'
{"schema_version":"friend-beta-access.v1","user_id":"smoke-friend","access_code":"mgb_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
JSON
chmod 600 "$ROOT/access.json"
: > "$ROOT/curl.log"
: > "$ROOT/curl-args.log"

output="$(
  PATH="$ROOT/bin:$PATH" \
  PYTHONPATH="$ROOT/python" \
  FRIEND_BETA_TEST_CURL_LOG="$ROOT/curl.log" \
  FRIEND_BETA_TEST_CURL_ARGS_LOG="$ROOT/curl-args.log" \
  MAGISTRATE_FRIEND_BETA_GATEWAY_URL='https://beta.example.test/api/v1' \
  MAGISTRATE_FRIEND_BETA_ACCESS_FILE="$ROOT/access.json" \
  MAGISTRATE_SMOKE_PYTHON="$(command -v python3)" \
  bash scripts/smoke_friend_beta.sh
)"
grep -Fq 'Friend Beta external smoke passed' <<<"$output"
grep -Fxq 'https://beta.example.test/api/v1/auth/friend-beta/session' "$ROOT/curl.log"
grep -Fxq 'https://beta.example.test/api/v1/auth/session' "$ROOT/curl.log"
grep -Fxq 'https://beta.example.test/api/v1/health' "$ROOT/curl.log"
grep -Fxq 'https://beta.example.test/api/v1/magi/conversations/current?limit=1' "$ROOT/curl.log"
grep -Fxq 'https://beta.example.test/api/v1/auth/session/revoke' "$ROOT/curl.log"
if grep -Eq 'mgb_|smoke-session-token' "$ROOT/curl.log" || grep -Eq 'mgb_|smoke-session-token' "$ROOT/curl-args.log" || grep -Eq 'mgb_|smoke-session-token' <<<"$output"; then
  echo 'Friend Beta smoke test leaked credential material to output or process arguments' >&2
  exit 1
fi

if PATH="$ROOT/bin:$PATH" PYTHONPATH="$ROOT/python" FRIEND_BETA_TEST_CURL_LOG="$ROOT/curl.log" \
  FRIEND_BETA_TEST_CURL_ARGS_LOG="$ROOT/curl-args.log" \
  MAGISTRATE_FRIEND_BETA_GATEWAY_URL='http://localhost:8000/api/v1' \
  MAGISTRATE_FRIEND_BETA_ACCESS_FILE="$ROOT/access.json" \
  MAGISTRATE_SMOKE_PYTHON="$(command -v python3)" \
  bash scripts/smoke_friend_beta.sh >/dev/null 2>&1; then
  echo 'Friend Beta smoke accepted a local/plain-HTTP endpoint' >&2
  exit 1
fi

chmod 644 "$ROOT/access.json"
if PATH="$ROOT/bin:$PATH" PYTHONPATH="$ROOT/python" FRIEND_BETA_TEST_CURL_LOG="$ROOT/curl.log" \
  FRIEND_BETA_TEST_CURL_ARGS_LOG="$ROOT/curl-args.log" \
  MAGISTRATE_FRIEND_BETA_GATEWAY_URL='https://beta.example.test/api/v1' \
  MAGISTRATE_FRIEND_BETA_ACCESS_FILE="$ROOT/access.json" \
  MAGISTRATE_SMOKE_PYTHON="$(command -v python3)" \
  bash scripts/smoke_friend_beta.sh >/dev/null 2>&1; then
  echo 'Friend Beta smoke accepted an overly broad access file' >&2
  exit 1
fi

echo 'Friend Beta external smoke contract passed'
