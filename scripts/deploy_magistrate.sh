#!/usr/bin/env bash
set -euo pipefail

# Guarded, manual production update. It only fast-forwards a clean deployment
# checkout and never resets, stashes, or overwrites local changes.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
SERVICE="${MAGISTRATE_SERVICE:-magistrate-gateway.service}"
PI_SERVICE="${MAGISTRATE_PI_SERVICE:-magistrate-captain-pi.service}"
READINESS_URL="${MAGISTRATE_READINESS_URL:-http://127.0.0.1:8000/api/v1/health}"
HEALTH_URL="${MAGISTRATE_HEALTH_URL:-$READINESS_URL}"
READINESS_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_TIMEOUT_SECONDS:-30}"
READINESS_INTERVAL_SECONDS="${MAGISTRATE_READINESS_INTERVAL_SECONDS:-1}"
READINESS_CURL_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS:-2}"
PI_READINESS_TIMEOUT_SECONDS="${MAGISTRATE_PI_READINESS_TIMEOUT_SECONDS:-30}"
LOCK_PATH="${MAGISTRATE_DEPLOY_LOCK:-${TMPDIR:-/tmp}/magistrate-deploy.lock}"

is_positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
is_nonnegative_integer() { [[ "$1" =~ ^[0-9]+$ ]]; }

if ! is_positive_integer "$READINESS_TIMEOUT_SECONDS"; then
  echo "refusing deploy: MAGISTRATE_READINESS_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 1
fi
if ! is_nonnegative_integer "$READINESS_INTERVAL_SECONDS"; then
  echo "refusing deploy: MAGISTRATE_READINESS_INTERVAL_SECONDS must be a non-negative integer" >&2
  exit 1
fi
if ! is_positive_integer "$READINESS_CURL_TIMEOUT_SECONDS"; then
  echo "refusing deploy: MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 1
fi
if ! is_positive_integer "$PI_READINESS_TIMEOUT_SECONDS"; then
  echo "refusing deploy: MAGISTRATE_PI_READINESS_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 1
fi

if [[ ! -e "$DEPLOY_DIR/.git" ]]; then
  echo "deployment checkout not found: $DEPLOY_DIR" >&2
  exit 1
fi

# A merged push can start overlapping jobs. Serialize updates without creating
# a file in the checkout (which would make the clean-tree guard fail later).
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "refusing update: another deployment is already running" >&2
  exit 1
fi

status="$(git -C "$DEPLOY_DIR" status --porcelain=v1)"
if [[ -n "$status" ]]; then
  echo "refusing update: deployment checkout is dirty" >&2
  printf '%s\n' "$status" >&2
  exit 1
fi

git -C "$DEPLOY_DIR" fetch --prune origin main
remote_main="$(git -C "$DEPLOY_DIR" rev-parse origin/main)"
head="$(git -C "$DEPLOY_DIR" rev-parse HEAD)"
if git -C "$DEPLOY_DIR" merge-base --is-ancestor "$head" "$remote_main"; then
  git -C "$DEPLOY_DIR" merge --ff-only origin/main
elif git -C "$DEPLOY_DIR" merge-base --is-ancestor "$remote_main" "$head"; then
  echo "deployment checkout is ahead of origin/main; leaving its commits intact" >&2
else
  echo "refusing update: deployment checkout diverged from origin/main" >&2
  exit 1
fi
# All backup/deploy evidence must name the commit that will actually restart,
# not the pre-fast-forward HEAD used in the ancestry decision above.
head="$(git -C "$DEPLOY_DIR" rev-parse HEAD)"

ENV_FILE="$DEPLOY_DIR/gateway/.env"
if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
  echo "refusing deploy: missing or unsafe $ENV_FILE" >&2
  exit 1
fi
if [[ "$(stat -c '%u' "$ENV_FILE")" != "$(id -u)" || "$(stat -c '%a' "$ENV_FILE")" != 600 ]]; then
  echo "refusing deploy: the production environment file must be service-owned mode 0600" >&2
  exit 1
fi
if grep -Eq '^[[:space:]]+[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE"; then
  echo "refusing deploy: environment assignments must start at column one" >&2
  exit 1
fi
duplicate_env_keys="$(awk -F= '
  /^[A-Za-z_][A-Za-z0-9_]*=/ { count[$1]++ }
  END { for (key in count) if (count[key] > 1) print key }
' "$ENV_FILE" | LC_ALL=C sort)"
if [[ -n "$duplicate_env_keys" ]]; then
  echo "refusing deploy: duplicate environment assignments are ambiguous" >&2
  printf '%s\n' "$duplicate_env_keys" >&2
  exit 1
fi

# Authentication and encrypted credentials depend on production-only settings.
# Fail before the build/restart rather than allowing systemd to start a process
# that silently falls back to a checkout-local database or development mode.
required_env=(MAGISTRATE_ENV MAGISTRATE_DB_PATH MAGISTRATE_BOOTSTRAP_SECRET MAGISTRATE_SECRET_KEY MAGISTRATE_CORS_ORIGINS)
for key in "${required_env[@]}"; do
  if ! grep -Eq "^${key}=[^#[:space:]]" "$ENV_FILE"; then
    echo "refusing deploy: $key is missing or empty in $ENV_FILE" >&2
    exit 1
  fi
done
if ! grep -Eq '^MAGISTRATE_ENV=production([[:space:]]|$)' "$ENV_FILE"; then
  echo "refusing deploy: MAGISTRATE_ENV must be production" >&2
  exit 1
fi
env_value() {
  local key=$1 value
  value="$(awk -v key="$key" '$0 ~ ("^" key "=") { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE")"
  value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

DB_PATH="$(env_value MAGISTRATE_DB_PATH)"
if [[ "$DB_PATH" != /* || "$DB_PATH" == "$DEPLOY_DIR"/* ]]; then
  echo "refusing deploy: MAGISTRATE_DB_PATH must be an absolute path outside the deployment checkout" >&2
  exit 1
fi
CORS_ORIGINS="$(awk -F= '$1 == "MAGISTRATE_CORS_ORIGINS" { sub(/^[[:space:]]+/, "", $2); print $2; exit }' "$ENV_FILE")"
CORS_ORIGINS="${CORS_ORIGINS%\"}"; CORS_ORIGINS="${CORS_ORIGINS#\"}"
if [[ "$CORS_ORIGINS" == *\** ]]; then
  echo "refusing deploy: MAGISTRATE_CORS_ORIGINS must contain explicit origins" >&2
  exit 1
fi
IFS=',' read -r -a cors_origins <<< "$CORS_ORIGINS"
for origin in "${cors_origins[@]}"; do
  origin="$(printf '%s' "$origin" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [[ ! "$origin" =~ ^https://[^[:space:],]+$ ]]; then
    echo "refusing deploy: MAGISTRATE_CORS_ORIGINS must contain HTTPS origins" >&2
    exit 1
  fi
done

# Native provider chat and the compatibility captain transport are deliberately
# mutually exclusive. Defaults are the production cutover state; rollback sets
# the first false and the second true without reverting schema or data.
env_boolean() {
  local key=$1 fallback=$2 present=false value
  grep -Eq "^${key}=" "$ENV_FILE" && present=true
  value="$(env_value "$key")"
  if [[ "$present" == false ]]; then printf '%s' "$fallback"; return; fi
  case "${value,,}" in
    1|true|yes|on) printf 'true' ;;
    0|false|no|off) printf 'false' ;;
    *) echo "refusing deploy: $key must be a supported boolean literal" >&2; return 1 ;;
  esac
}
NATIVE_CHAT_ENABLED="$(env_boolean MAGISTRATE_NATIVE_CHAT_ENABLED true)"
LEGACY_CHAT_ENABLED="$(env_boolean MAGISTRATE_LEGACY_CHAT_ENABLED false)"
if [[ "$NATIVE_CHAT_ENABLED" == "$LEGACY_CHAT_ENABLED" ]]; then
  echo "refusing deploy: exactly one of native chat and legacy chat must be enabled" >&2
  exit 1
fi
if [[ "$NATIVE_CHAT_ENABLED" == true && -z "$(env_value OPENAI_API_KEY)" ]]; then
  echo "refusing deploy: enabled native chat requires OPENAI_API_KEY" >&2
  exit 1
fi

# Pi remains default-on only *inside* explicit legacy rollback. Native normal
# operation treats an absent Pi flag as off and refuses an inconsistent true.
PI_FLAG_PRESENT=false
if grep -Eq '^MAGISTRATE_PI_OWNERSHIP_ENABLED=' "$ENV_FILE"; then
  PI_FLAG_PRESENT=true
fi
PI_FLAG="$(env_value MAGISTRATE_PI_OWNERSHIP_ENABLED)"
if [[ "$PI_FLAG_PRESENT" == false ]]; then
  PI_ENABLED="$LEGACY_CHAT_ENABLED"
else
  case "${PI_FLAG,,}" in
    1|true|yes|on) PI_ENABLED=true ;;
    0|false|no|off) PI_ENABLED=false ;;
    *)
      echo "refusing deploy: MAGISTRATE_PI_OWNERSHIP_ENABLED must be a supported boolean literal" >&2
      exit 1
      ;;
  esac
fi
if [[ "$LEGACY_CHAT_ENABLED" == false && "$PI_ENABLED" == true ]]; then
  echo "refusing deploy: Pi ownership cannot be enabled while legacy chat is disabled" >&2
  exit 1
fi

if [[ "$PI_ENABLED" == true ]]; then
  pi_required_env=(
    MAGISTRATE_PI_RUNTIME_DIR MAGISTRATE_PI_IPC_KEY_PATH
    MAGISTRATE_PI_ADAPTER_SOCKET MAGISTRATE_PI_ADAPTER_JOURNAL
    MAGISTRATE_PI_ADAPTER_UID MAGISTRATE_PI_CAPABILITY_TTL_SECONDS
    MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS
    MAGISTRATE_PI_RECOVERY_SECONDS
  )
  for key in "${pi_required_env[@]}"; do
    if ! grep -Eq "^${key}=[^#[:space:]]" "$ENV_FILE"; then
      echo "refusing deploy: enabled Pi ownership requires $key" >&2
      exit 1
    fi
  done
  PI_RUNTIME_DIR="$(env_value MAGISTRATE_PI_RUNTIME_DIR)"
  PI_KEY_PATH="$(env_value MAGISTRATE_PI_IPC_KEY_PATH)"
  PI_SOCKET_PATH="$(env_value MAGISTRATE_PI_ADAPTER_SOCKET)"
  PI_JOURNAL_PATH="$(env_value MAGISTRATE_PI_ADAPTER_JOURNAL)"
  PI_ADAPTER_UID="$(env_value MAGISTRATE_PI_ADAPTER_UID)"
  PI_CAPABILITY_TTL="$(env_value MAGISTRATE_PI_CAPABILITY_TTL_SECONDS)"
  PI_CONNECT_TIMEOUT="$(env_value MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS)"
  PI_RESPONSE_TIMEOUT="$(env_value MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS)"
  PI_RECOVERY_SECONDS="$(env_value MAGISTRATE_PI_RECOVERY_SECONDS)"
  if [[ ! "$PI_ADAPTER_UID" =~ ^[0-9]+$ || "$PI_ADAPTER_UID" != "$(id -u)" ]]; then
    echo "refusing deploy: MAGISTRATE_PI_ADAPTER_UID must equal the deployment service UID" >&2
    exit 1
  fi
  if [[ -z "$PI_SERVICE" || "$PI_SERVICE" == "$SERVICE" || "$PI_SERVICE" != *.service \
        || "${PI_SERVICE,,}" == *herdr* || "${PI_SERVICE,,}" == *firstmate* ]]; then
    echo "refusing deploy: MAGISTRATE_PI_SERVICE must name only the dedicated captain Pi service" >&2
    exit 1
  fi
fi

# The semantic producer remains optional until an operator explicitly enables
# the rollout flag. A configured code root is always verified against the
# immutable reviewed pin; required mode additionally demands the separately
# activated, valid home-local outbox before any build or service restart.
CAPTAIN_REQUIRED="$(env_value MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED)"
CAPTAIN_REQUIRED="${CAPTAIN_REQUIRED:-false}"
case "${CAPTAIN_REQUIRED,,}" in
  1|true|yes|on) CAPTAIN_REQUIRED=true ;;
  0|false|no|off) CAPTAIN_REQUIRED=false ;;
  *)
    echo "refusing deploy: MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED must be a boolean literal" >&2
    exit 1
    ;;
esac
FIRSTMATE_ROOT="$(env_value MAGISTRATE_FIRSTMATE_ROOT)"
FIRSTMATE_HOME="$(env_value FM_HOME)"
if [[ -n "$FIRSTMATE_ROOT" ]]; then
  if [[ "$FIRSTMATE_ROOT" != /* ]]; then
    echo "refusing deploy: MAGISTRATE_FIRSTMATE_ROOT must be absolute" >&2
    exit 1
  fi
  "$DEPLOY_DIR/scripts/install_firstmate_producer.sh" verify --root "$FIRSTMATE_ROOT"
fi
if [[ "$CAPTAIN_REQUIRED" == true ]]; then
  if [[ -z "$FIRSTMATE_ROOT" || -z "$FIRSTMATE_HOME" || "$FIRSTMATE_HOME" != /* ]]; then
    echo "refusing deploy: required captain producer needs absolute FM_HOME and MAGISTRATE_FIRSTMATE_ROOT" >&2
    exit 1
  fi
  "$DEPLOY_DIR/scripts/install_firstmate_producer.sh" ready \
    --root "$FIRSTMATE_ROOT" --fm-home "$FIRSTMATE_HOME"
fi

if [[ "$PI_ENABLED" == true ]]; then
  command -v python3 >/dev/null 2>&1 || {
    echo "refusing deploy: python3 is required for Pi ownership preflight" >&2
    exit 1
  }
  # Import only the secret-free local IPC validator and pass only Pi settings.
  # It creates a missing key without ever returning or printing key material.
  env \
    "MAGISTRATE_PI_RUNTIME_DIR=$PI_RUNTIME_DIR" \
    "MAGISTRATE_PI_IPC_KEY_PATH=$PI_KEY_PATH" \
    "MAGISTRATE_PI_ADAPTER_SOCKET=$PI_SOCKET_PATH" \
    "MAGISTRATE_PI_ADAPTER_JOURNAL=$PI_JOURNAL_PATH" \
    "MAGISTRATE_PI_ADAPTER_UID=$PI_ADAPTER_UID" \
    "MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS=$PI_CONNECT_TIMEOUT" \
    "MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS=$PI_RESPONSE_TIMEOUT" \
    "MAGISTRATE_PI_CAPABILITY_TTL_SECONDS=$PI_CAPABILITY_TTL" \
    "MAGISTRATE_PI_RECOVERY_SECONDS=$PI_RECOVERY_SECONDS" \
    "PYTHONDONTWRITEBYTECODE=1" \
    "PYTHONPATH=$DEPLOY_DIR/gateway" \
    python3 - <<'PY'
import os
from app.pi_adapter_ipc import PiAdapterClient, PiAdapterIPCError
try:
    ttl = int(os.environ['MAGISTRATE_PI_CAPABILITY_TTL_SECONDS'])
    recovery = int(os.environ['MAGISTRATE_PI_RECOVERY_SECONDS'])
    if not 5 <= ttl <= 600 or not 1 <= recovery <= 60:
        raise ValueError
    PiAdapterClient.from_environment().ensure_key()
except (KeyError, ValueError):
    raise SystemExit('Pi ownership preflight failed: invalid-timeout') from None
except PiAdapterIPCError as exc:
    raise SystemExit(f'Pi ownership preflight failed: {exc.code}') from None
PY

fi

# Any schema-affecting native rollout requires an integrity-checked online
# SQLite backup first. The same guard continues to protect Pi-only rollouts.
if [[ "$NATIVE_CHAT_ENABLED" == true || "$PI_ENABLED" == true ]]; then
  if [[ ! -f "$DB_PATH" || -L "$DB_PATH" ]]; then
    echo "refusing deploy: native/Pi rollout requires an existing regular persistent SQLite database" >&2
    exit 1
  fi
  DB_REAL_PATH="$(realpath -e -- "$DB_PATH" 2>/dev/null || true)"
  DEPLOY_REAL_PATH="$(realpath -e -- "$DEPLOY_DIR" 2>/dev/null || true)"
  if [[ -z "$DB_REAL_PATH" || -z "$DEPLOY_REAL_PATH" || "$DB_REAL_PATH" != "$DB_PATH" \
        || "$DB_REAL_PATH" == "$DEPLOY_REAL_PATH"/* ]]; then
    echo "refusing deploy: persistent SQLite path must be normalized, symlink-free, and outside the checkout" >&2
    exit 1
  fi
  if [[ "$(stat -c '%u' "$DB_PATH")" != "$(id -u)" || "$(stat -c '%a' "$DB_PATH")" != 600 ]]; then
    echo "refusing deploy: persistent SQLite must be owned by the service UID with mode 0600" >&2
    exit 1
  fi
  BACKUP_DIR="${MAGISTRATE_BACKUP_DIR:-$(dirname "$DB_PATH")/backups}"
  if [[ "$BACKUP_DIR" != /* || "$BACKUP_DIR" == "$DEPLOY_DIR"/* || -L "$BACKUP_DIR" ]]; then
    echo "refusing deploy: MAGISTRATE_BACKUP_DIR must be an absolute non-symlink path outside the checkout" >&2
    exit 1
  fi
  BACKUP_PARENT="$(dirname "$BACKUP_DIR")"
  BACKUP_PARENT_REAL="$(realpath -e -- "$BACKUP_PARENT" 2>/dev/null || true)"
  if [[ -z "$BACKUP_PARENT_REAL" || "$BACKUP_PARENT_REAL" != "$BACKUP_PARENT" \
        || "$BACKUP_PARENT_REAL" == "$DEPLOY_REAL_PATH" \
        || "$BACKUP_PARENT_REAL" == "$DEPLOY_REAL_PATH"/* ]]; then
    echo "refusing deploy: backup parent must exist, be normalized, symlink-free, and outside the checkout" >&2
    exit 1
  fi
  install -d -m 700 "$BACKUP_DIR"
  BACKUP_REAL_PATH="$(realpath -e -- "$BACKUP_DIR" 2>/dev/null || true)"
  if [[ -z "$BACKUP_REAL_PATH" || "$BACKUP_REAL_PATH" != "$BACKUP_DIR" \
        || "$BACKUP_REAL_PATH" == "$DEPLOY_REAL_PATH"/* ]]; then
    echo "refusing deploy: backup directory path must be normalized, symlink-free, and outside the checkout" >&2
    exit 1
  fi
  if [[ "$(stat -c '%u' "$BACKUP_DIR")" != "$(id -u)" || "$(stat -c '%a' "$BACKUP_DIR")" != 700 ]]; then
    echo "refusing deploy: backup directory must be owned by the service UID with mode 0700" >&2
    exit 1
  fi
  BACKUP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  if [[ "$NATIVE_CHAT_ENABLED" == true ]]; then
    BACKUP_PREFIX="magistrate-pre-native"
    BACKUP_REASON="Native Magi"
  else
    BACKUP_PREFIX="magistrate-pre-pi"
    BACKUP_REASON="Pi ownership"
  fi
  BACKUP_PATH="$BACKUP_DIR/${BACKUP_PREFIX}-${BACKUP_STAMP}-${head:0:12}.sqlite3"
  if [[ -e "$BACKUP_PATH" || -e "$BACKUP_PATH.commit" || -e "$BACKUP_PATH.sha256" ]]; then
    echo "refusing deploy: the SQLite backup destination already exists" >&2
    exit 1
  fi
  python3 - "$DB_PATH" "$BACKUP_PATH" <<'PY'
import sqlite3
import sys

def counts(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: connection.execute(
        'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
    ).fetchone()[0] for name in tables}

with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as backup:
    source.backup(backup)
    source_counts = counts(source)
with sqlite3.connect(sys.argv[2]) as backup:
    if backup.execute('PRAGMA integrity_check').fetchone() != ('ok',):
        raise SystemExit('SQLite backup integrity check failed')
    if counts(backup) != source_counts:
        raise SystemExit('SQLite backup row-count verification failed')
PY
  chmod 600 "$BACKUP_PATH"
  (umask 077; printf '%s\n' "$head" > "$BACKUP_PATH.commit")
  (umask 077; sha256sum "$BACKUP_PATH" | awk '{print $1}' > "$BACKUP_PATH.sha256")
  echo "$BACKUP_REASON backup verified for deployed commit $head"
fi

if [[ "$PI_ENABLED" == true ]]; then
  command -v pi >/dev/null 2>&1 || {
    echo "refusing deploy: pi CLI is required to install the ownership extension" >&2
    exit 1
  }
  command -v ss >/dev/null 2>&1 || {
    echo "refusing deploy: ss is required to prove the listening ownership socket" >&2
    exit 1
  }
  (
    cd "$DEPLOY_DIR/pi-extension"
    npm ci
    npm run typecheck
    npm test
  )
fi

command -v curl >/dev/null 2>&1 || {
  echo "refusing deploy: curl is required for gateway readiness verification" >&2
  exit 1
}

(
  cd "$DEPLOY_DIR/frontend"
  npm ci
  EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED="$NATIVE_CHAT_ENABLED" \
    EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED="$LEGACY_CHAT_ENABLED" \
    npx expo export -p web
)

for asset in index.html chat.html voice.html; do
  if [[ ! -f "$DEPLOY_DIR/frontend/dist/$asset" ]]; then
    echo "refusing deploy: frontend export did not produce frontend/dist/$asset" >&2
    exit 1
  fi
done

if [[ "$PI_ENABLED" == true ]]; then
  # Persist package activation only after every build/test preflight passed.
  # Installation does not mutate the currently running Pi process.
  (
    cd "$DEPLOY_DIR/pi-extension"
    pi install "$(pwd)"
  )
  # Load the freshly installed extension before restarting Gateway. This keeps
  # the signed probe compatible during upgrades while leaving Herdr untouched.
  systemctl --user restart "$PI_SERVICE"
  pi_readiness_deadline=$((SECONDS + PI_READINESS_TIMEOUT_SECONDS))
  pi_peer_pid=""
  while :; do
    if pi_peer_pid="$(env \
      "MAGISTRATE_PI_RUNTIME_DIR=$PI_RUNTIME_DIR" \
      "MAGISTRATE_PI_IPC_KEY_PATH=$PI_KEY_PATH" \
      "MAGISTRATE_PI_ADAPTER_SOCKET=$PI_SOCKET_PATH" \
      "MAGISTRATE_PI_ADAPTER_JOURNAL=$PI_JOURNAL_PATH" \
      "MAGISTRATE_PI_ADAPTER_UID=$PI_ADAPTER_UID" \
      "MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS=$PI_CONNECT_TIMEOUT" \
      "MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS=$PI_RESPONSE_TIMEOUT" \
      "PYTHONDONTWRITEBYTECODE=1" \
      "PYTHONPATH=$DEPLOY_DIR/gateway" \
      python3 - <<'PY'
import asyncio
from app.pi_adapter_ipc import PiAdapterClient, PiAdapterIPCError
try:
    print(asyncio.run(PiAdapterClient.from_environment().probe()))
except PiAdapterIPCError:
    raise SystemExit(1) from None
PY
    )"; then
      [[ "$pi_peer_pid" =~ ^[1-9][0-9]*$ ]] && break
    fi
    if (( SECONDS >= pi_readiness_deadline )); then
      pi_service_state="$(systemctl --user is-active "$PI_SERVICE" 2>/dev/null || true)"
      pi_service_state="${pi_service_state:-unknown}"
      echo "captain Pi adapter readiness timed out (dedicated service state $pi_service_state)" >&2
      exit 1
    fi
    sleep "$READINESS_INTERVAL_SECONDS"
  done
  if [[ "$(systemctl --user is-active "$PI_SERVICE" 2>/dev/null || true)" != active ]]; then
    echo "captain Pi adapter socket answered but its dedicated service is not active" >&2
    exit 1
  fi
  pi_main_pid="$(systemctl --user show "$PI_SERVICE" --property MainPID --value 2>/dev/null || true)"
  if [[ ! "$pi_main_pid" =~ ^[1-9][0-9]*$ || "$pi_peer_pid" != "$pi_main_pid" ]]; then
    echo "captain Pi adapter peer is not the dedicated service MainPID" >&2
    exit 1
  fi
  ss -xlH | grep -F -- "$PI_SOCKET_PATH" >/dev/null || {
    echo "captain Pi adapter path is not a listening Unix socket" >&2
    exit 1
  }
  echo "dedicated captain Pi restart loaded the ownership extension; trusted socket is listening"
fi

systemctl --user restart "$SERVICE"

# systemd's unit state can briefly be inactive while a restart is in flight.
# Poll the HTTP endpoint instead: 2xx proves the application answered, while
# 401/403 proves the protected production process is reachable without a secret.
# A 000 (connection refused) is expected during the bounded restart window.
readiness_started="$SECONDS"
readiness_deadline=$((readiness_started + READINESS_TIMEOUT_SECONDS))
readiness_attempt=0
last_health_status="unreachable"
while :; do
  readiness_attempt=$((readiness_attempt + 1))
  health_status="$(curl --silent --connect-timeout "$READINESS_CURL_TIMEOUT_SECONDS" \
    --max-time "$READINESS_CURL_TIMEOUT_SECONDS" --output /dev/null \
    --write-out '%{http_code}' "$READINESS_URL" 2>/dev/null || true)"
  health_status="${health_status:-unreachable}"
  case "$health_status" in
    2??|401|403)
      readiness_elapsed=$((SECONDS - readiness_started))
      echo "gateway readiness verified after ${readiness_elapsed}s (attempt ${readiness_attempt}, HTTP ${health_status})"
      break
      ;;
  esac
  last_health_status="$health_status"
  if (( SECONDS >= readiness_deadline )); then
    service_state="$(systemctl --user is-active "$SERVICE" 2>/dev/null || true)"
    service_state="${service_state:-unknown}"
    echo "gateway readiness timed out after ${READINESS_TIMEOUT_SECONDS}s (attempts ${readiness_attempt}; last HTTP response ${last_health_status}; systemd state ${service_state})" >&2
    exit 1
  fi
  sleep "$READINESS_INTERVAL_SECONDS"
done

if [[ "$PI_ENABLED" == true ]]; then
  # Fixed state counts only: never select encrypted dispatch material or native
  # source identity while proving the rollout.
  python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys
states = ('prepared', 'bound', 'finalized', 'failed')
with sqlite3.connect(sys.argv[1]) as connection:
    observed = dict(connection.execute(
        "SELECT state, COUNT(*) FROM pi_semantic_dispatches "
        "WHERE state IN ('prepared','bound','finalized','failed') GROUP BY state"
    ))
    backlog = connection.execute(
        "SELECT COUNT(*) FROM pi_semantic_dispatches "
        "WHERE state IN ('prepared','bound') OR "
        "(state IN ('finalized','failed') AND adapter_acknowledged_at IS NULL)"
    ).fetchone()[0]
print('Pi ownership dispatch counts: '
      + ' '.join(f'{state}={int(observed.get(state, 0))}' for state in states)
      + f' recovery_backlog={int(backlog)}')
PY
fi

# GitHub Actions intentionally performs only the unauthenticated reachability
# check above. A trusted operator can opt into the complete smoke, which reads
# the bootstrap secret on the host and checks issuance, authenticated health,
# and one Herdr-backed application endpoint without logging credentials.
if [[ "${MAGISTRATE_TRUSTED_SMOKE:-0}" == "1" ]]; then
  MAGISTRATE_DEPLOY_DIR="$DEPLOY_DIR" MAGISTRATE_HEALTH_URL="$HEALTH_URL" \
    "$DEPLOY_DIR/scripts/smoke_magistrate.sh"
fi

echo "deployed $(git -C "$DEPLOY_DIR" rev-parse --short HEAD) with frontend dist from $DEPLOY_DIR/frontend/dist"
