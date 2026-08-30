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
mkdir -p "$SEED/frontend" "$SEED/gateway"
cat > "$SEED/gateway/.env" <<EOF
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=$ROOT/state/magistrate.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=test-bootstrap
MAGISTRATE_SECRET_KEY=test-secret
MAGISTRATE_CORS_ORIGINS=https://demo.example.invalid
EOF
printf '{}\n' > "$SEED/frontend/package-lock.json"
printf '{}\n' > "$SEED/frontend/package.json"
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
printf '401'
EOF
chmod +x "$STUBS"/*

run_update() {
  PATH="$STUBS:$PATH" MAGISTRATE_DEPLOY_DIR="$DEPLOY" MAGISTRATE_DEPLOY_LOCK="$ROOT/deploy.lock" \
    bash scripts/deploy_magistrate.sh
}

run_update >/dev/null
test "$(git -C "$DEPLOY" rev-parse HEAD)" = "$(git -C "$REMOTE" rev-parse refs/heads/main)"

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

echo 'deployment safeguards passed'
