#!/usr/bin/env bash
set -euo pipefail

# Guarded, manual production update. It only fast-forwards a clean deployment
# checkout and never resets, stashes, or overwrites local changes.
DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
SERVICE="${MAGISTRATE_SERVICE:-magistrate-gateway.service}"

if [[ ! -d "$DEPLOY_DIR/.git" ]]; then
  echo "deployment checkout not found: $DEPLOY_DIR" >&2
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

systemctl --user restart "$SERVICE"
systemctl --user is-active --quiet "$SERVICE"
echo "deployed $(git -C "$DEPLOY_DIR" rev-parse --short HEAD) with frontend dist from $DEPLOY_DIR/frontend/dist"
