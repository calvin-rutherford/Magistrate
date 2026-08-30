#!/usr/bin/env bash
set -euo pipefail

# Guarded, manual production update. It only fast-forwards a clean deployment
# checkout and never resets, stashes, or overwrites local changes.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
SERVICE="${MAGISTRATE_SERVICE:-magistrate-gateway.service}"
READINESS_URL="${MAGISTRATE_READINESS_URL:-http://127.0.0.1:8000/api/v1/health}"
HEALTH_URL="${MAGISTRATE_HEALTH_URL:-$READINESS_URL}"
READINESS_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_TIMEOUT_SECONDS:-30}"
READINESS_INTERVAL_SECONDS="${MAGISTRATE_READINESS_INTERVAL_SECONDS:-1}"
READINESS_CURL_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS:-2}"
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

ENV_FILE="$DEPLOY_DIR/gateway/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "refusing deploy: missing $ENV_FILE" >&2
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
DB_PATH="$(awk -F= '$1 == "MAGISTRATE_DB_PATH" { sub(/^[[:space:]]+/, "", $2); print $2; exit }' "$ENV_FILE")"
DB_PATH="${DB_PATH%\"}"; DB_PATH="${DB_PATH#\"}"
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

command -v curl >/dev/null 2>&1 || {
  echo "refusing deploy: curl is required for gateway readiness verification" >&2
  exit 1
}

(
  cd "$DEPLOY_DIR/frontend"
  npm ci
  npx expo export -p web
)

for asset in index.html chat.html voice.html; do
  if [[ ! -f "$DEPLOY_DIR/frontend/dist/$asset" ]]; then
    echo "refusing deploy: frontend export did not produce frontend/dist/$asset" >&2
    exit 1
  fi
done

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

# GitHub Actions intentionally performs only the unauthenticated reachability
# check above. A trusted operator can opt into the complete smoke, which reads
# the bootstrap secret on the host and checks issuance, authenticated health,
# and one Herdr-backed application endpoint without logging credentials.
if [[ "${MAGISTRATE_TRUSTED_SMOKE:-0}" == "1" ]]; then
  MAGISTRATE_DEPLOY_DIR="$DEPLOY_DIR" MAGISTRATE_HEALTH_URL="$HEALTH_URL" \
    "$DEPLOY_DIR/scripts/smoke_magistrate.sh"
fi

echo "deployed $(git -C "$DEPLOY_DIR" rev-parse --short HEAD) with frontend dist from $DEPLOY_DIR/frontend/dist"
