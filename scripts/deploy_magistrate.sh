#!/usr/bin/env bash
set -euo pipefail

# Guarded, manual production update. It only fast-forwards a clean deployment
# checkout and never resets, stashes, or overwrites local changes.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
SERVICE="${MAGISTRATE_SERVICE:-magistrate-gateway.service}"
HEALTH_URL="${MAGISTRATE_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"
LOCK_PATH="${MAGISTRATE_DEPLOY_LOCK:-${TMPDIR:-/tmp}/magistrate-deploy.lock}"

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
systemctl --user is-active --quiet "$SERVICE"
# An authenticated health response is expected to be 200 in production, but a
# 401/403 still proves the freshly restarted HTTP process is reachable without
# putting the device token in deployment logs or GitHub secrets.
if command -v curl >/dev/null 2>&1; then
  health_status="$(curl --silent --show-error --connect-timeout 5 --max-time 10 --output /dev/null --write-out '%{http_code}' "$HEALTH_URL" || true)"
  case "$health_status" in
    2??|401|403) ;;
    *) echo "deployment service is active but health endpoint returned HTTP ${health_status:-unreachable}" >&2; exit 1 ;;
  esac
fi

echo "deployed $(git -C "$DEPLOY_DIR" rev-parse --short HEAD) with frontend dist from $DEPLOY_DIR/frontend/dist"
