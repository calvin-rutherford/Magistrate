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

if [[ ! -f "$DEPLOY_DIR/gateway/.env" ]]; then
  echo "refusing deploy: missing $DEPLOY_DIR/gateway/.env" >&2
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
