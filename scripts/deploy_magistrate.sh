#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

# Native Gateway only. Legacy Django/Celery/Pi transports are NOT a rollback.
# This updates only a clean release checkout; it never resets/stashes work or
# manages worker/Herdr lifecycle. An operator owns rollback to a reviewed commit.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
SERVICE="${MAGISTRATE_SERVICE:-magistrate-gateway.service}"
READINESS_URL="${MAGISTRATE_READINESS_URL:-http://127.0.0.1:8000/readyz}"
READINESS_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_TIMEOUT_SECONDS:-30}"
READINESS_INTERVAL_SECONDS="${MAGISTRATE_READINESS_INTERVAL_SECONDS:-1}"
READINESS_CURL_TIMEOUT_SECONDS="${MAGISTRATE_READINESS_CURL_TIMEOUT_SECONDS:-2}"
LOCK_PATH="${MAGISTRATE_DEPLOY_LOCK:-${TMPDIR:-/tmp}/magistrate-deploy.lock}"

for value in "$READINESS_TIMEOUT_SECONDS" "$READINESS_CURL_TIMEOUT_SECONDS"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo 'refusing deploy: invalid timeout' >&2; exit 1; }
done
[[ "$READINESS_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || { echo 'refusing deploy: invalid interval' >&2; exit 1; }
[[ "$SERVICE" =~ ^magistrate-gateway(-[a-z0-9]+)?\.service$ ]] || {
  echo 'refusing deploy: only a dedicated Magistrate Gateway service may be restarted' >&2; exit 1;
}
[[ -e "$DEPLOY_DIR/.git" ]] || { echo 'refusing deploy: release checkout missing' >&2; exit 1; }
exec 9>"$LOCK_PATH"
flock -n 9 || { echo 'refusing deploy: another deployment is running' >&2; exit 1; }
[[ -z "$(git -C "$DEPLOY_DIR" status --porcelain=v1)" ]] || {
  echo 'refusing deploy: deployment checkout is dirty' >&2; exit 1;
}
git -C "$DEPLOY_DIR" fetch --prune origin main
head="$(git -C "$DEPLOY_DIR" rev-parse HEAD)"
remote="$(git -C "$DEPLOY_DIR" rev-parse origin/main)"
if git -C "$DEPLOY_DIR" merge-base --is-ancestor "$head" "$remote"; then
  git -C "$DEPLOY_DIR" merge --ff-only origin/main
elif git -C "$DEPLOY_DIR" merge-base --is-ancestor "$remote" "$head"; then
  echo 'release checkout is ahead of origin/main; preserving reviewed local commits'
else
  echo 'refusing deploy: release checkout diverged' >&2; exit 1
fi
head="$(git -C "$DEPLOY_DIR" rev-parse HEAD)"
ENV_FILE="$DEPLOY_DIR/gateway/.env"
(
  cd "$DEPLOY_DIR/gateway"
  uv sync --frozen --no-dev
  PYTHONPATH=. uv run --frozen python -m scripts.production_preflight "$ENV_FILE"
)
# Parse only the selected non-secret settings. No eval/source or shell expansion.
env_value() {
  PYTHONPATH="$DEPLOY_DIR/gateway" python3 - "$ENV_FILE" "$1" <<'PY'
from pathlib import Path
import sys
from scripts.production_preflight import load_service_environment
print(load_service_environment(Path(sys.argv[1])).get(sys.argv[2], ''))
PY
}
DB_PATH="$(env_value MAGISTRATE_DB_PATH)"
DATABASE_URL="$(env_value MAGISTRATE_DATABASE_URL)"
# Preflight validates backend exclusivity, external paths and private modes.
# PostgreSQL backup/restore authority stays with the managed database platform.
if [[ -n "$DATABASE_URL" && "$(env_value MAGISTRATE_POSTGRES_BACKUP_CONFIRMED)" != true ]]; then
  echo 'refusing deploy: PostgreSQL requires an acknowledged restorable snapshot' >&2; exit 1
fi
FIRSTMATE_ROOT="$(env_value MAGISTRATE_FIRSTMATE_ROOT)"
FIRSTMATE_HOME="$(env_value FM_HOME)"
CAPTAIN_REQUIRED="$(env_value MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED)"
case "${CAPTAIN_REQUIRED,,}" in
  ''|0|false|no|off) CAPTAIN_REQUIRED=false ;;
  1|true|yes|on) CAPTAIN_REQUIRED=true ;;
  *) echo 'refusing deploy: invalid captain-required boolean' >&2; exit 1 ;;
esac
if [[ -n "$FIRSTMATE_ROOT" ]]; then
  [[ "$FIRSTMATE_ROOT" == /* ]] || { echo 'refusing deploy: producer root must be absolute' >&2; exit 1; }
  "$DEPLOY_DIR/scripts/install_firstmate_producer.sh" verify --root "$FIRSTMATE_ROOT"
fi
if [[ "$CAPTAIN_REQUIRED" == true ]]; then
  [[ "$FIRSTMATE_ROOT" == /* && "$FIRSTMATE_HOME" == /* ]] || {
    echo 'refusing deploy: required captain producer needs absolute FM_HOME and MAGISTRATE_FIRSTMATE_ROOT' >&2; exit 1;
  }
  "$DEPLOY_DIR/scripts/install_firstmate_producer.sh" ready --root "$FIRSTMATE_ROOT" --fm-home "$FIRSTMATE_HOME"
fi

if [[ -z "$DATABASE_URL" ]]; then
BACKUP_DIR="${MAGISTRATE_BACKUP_DIR:-$(dirname "$DB_PATH")/backups}"
[[ "$BACKUP_DIR" == /* && "$BACKUP_DIR" != "$DEPLOY_DIR"/* && ! -L "$BACKUP_DIR" ]] || {
  echo 'refusing deploy: unsafe backup directory' >&2; exit 1;
}
[[ "$(realpath -e -- "$(dirname "$BACKUP_DIR")")" == "$(dirname "$BACKUP_DIR")" ]] || {
  echo 'refusing deploy: unsafe backup parent' >&2; exit 1;
}
if [[ ! -d "$BACKUP_DIR" ]]; then mkdir -m 700 "$BACKUP_DIR"; fi
BACKUP_PATH="$BACKUP_DIR/magistrate-$(date -u +%Y%m%dT%H%M%S%N)-${head:0:12}.sqlite3"
python3 "$DEPLOY_DIR/gateway/scripts/storage_ops.py" backup "$DB_PATH" "$BACKUP_PATH" >/dev/null
printf '%s\n' "$head" > "$BACKUP_PATH.commit"
echo "Native Magi backup verified for deployed commit $head"
else
  echo "PostgreSQL pre-deploy snapshot acknowledged for commit $head"
fi

command -v curl >/dev/null
(
  cd "$DEPLOY_DIR/frontend"
  npm ci
  npx expo export -p web
)
for asset in index.html chat.html voice.html; do
  [[ -f "$DEPLOY_DIR/frontend/dist/$asset" ]] || {
    echo 'refusing deploy: incomplete frontend export' >&2; exit 1;
  }
done
systemctl --user restart "$SERVICE"
# A 401/403 proves only reachability, NOT readiness. /readyz reports startup,
# readable persisted schema and configured provider without a live model/tool.
deadline=$((SECONDS + READINESS_TIMEOUT_SECONDS))
while :; do
  code="$(curl --silent --connect-timeout "$READINESS_CURL_TIMEOUT_SECONDS" \
    --max-time "$READINESS_CURL_TIMEOUT_SECONDS" --output /dev/null \
    --write-out '%{http_code}' "$READINESS_URL" 2>/dev/null || true)"
  if [[ "$code" == 200 ]]; then
    echo 'gateway readiness verified (HTTP 200)'
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "gateway readiness timed out (last HTTP response ${code:-unreachable}); backup retained, operator rollback required" >&2
    exit 1
  fi
  sleep "$READINESS_INTERVAL_SECONDS"
done
if [[ "${MAGISTRATE_TRUSTED_SMOKE:-0}" == 1 ]]; then
  MAGISTRATE_DEPLOY_DIR="$DEPLOY_DIR" "$DEPLOY_DIR/scripts/smoke_magistrate.sh"
fi
echo "deployed $head"
