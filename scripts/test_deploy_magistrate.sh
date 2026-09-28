#!/usr/bin/env bash
set -euo pipefail
# Hermetic release test: real SQLite/preflight, stubbed install/build/network and
# service restart. Never contacts a runtime or a deployment host.
REPO="$(pwd -P)"
ROOT="$(mktemp -d)"
trap 'code=$?; if (( code != 0 )) && [[ -f "$ROOT/output" ]]; then tail -60 "$ROOT/output" >&2; fi; rm -rf "$ROOT"' EXIT
REMOTE="$ROOT/remote.git"; SEED="$ROOT/seed"; DEPLOY="$ROOT/deploy"; STUBS="$ROOT/stubs"
mkdir -p "$STUBS" "$ROOT/state" "$SEED/gateway/app" "$SEED/gateway/scripts" "$SEED/frontend" "$SEED/scripts"
chmod 700 "$ROOT/state"
cp -R gateway/app/. "$SEED/gateway/app/"
cp gateway/billing_catalog.json "$SEED/gateway/"
printf '__pycache__/\n*.pyc\n' > "$SEED/.gitignore"
cp gateway/scripts/{production_preflight,storage_ops}.py "$SEED/gateway/scripts/"
cp scripts/smoke_magistrate.sh "$SEED/scripts/"
cat > "$SEED/scripts/install_firstmate_producer.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_ROOT/producer.log"
EOF
chmod +x "$SEED/scripts/"*.sh
printf 'dist/\n' > "$SEED/frontend/.gitignore"
printf '{}\n' > "$SEED/frontend/package-lock.json"
git init --bare "$REMOTE" >/dev/null
git init -b main "$SEED" >/dev/null
git -C "$SEED" config user.email test@example.invalid
git -C "$SEED" config user.name release-test
git -C "$SEED" add .; git -C "$SEED" commit -qm initial
git -C "$SEED" remote add origin "$REMOTE"; git -C "$SEED" push -qu origin main
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main
git clone -q "$REMOTE" "$DEPLOY"
printf 'gateway/.env\n' >> "$DEPLOY/.git/info/exclude"
cat > "$DEPLOY/gateway/.env" <<EOF
MAGISTRATE_ENV=production
MAGISTRATE_DB_PATH=$ROOT/state/db.sqlite3
MAGISTRATE_BOOTSTRAP_SECRET=a-long-random-looking-test-authority-not-real-123456
MAGISTRATE_SECRET_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
MAGISTRATE_CORS_ORIGINS=https://app.magistrate.com
OPENAI_API_KEY=synthetic-provider-secret
EOF
chmod 600 "$DEPLOY/gateway/.env"
python3 - "$ROOT/state/db.sqlite3" <<'PY'
import os, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute('CREATE TABLE retained(id TEXT)')
    conn.execute("INSERT INTO retained VALUES ('legacy-preserved')")
os.chmod(sys.argv[1], 0o600)
PY
cat > "$STUBS/uv" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == sync ]]; then exit 0; fi
shift
[[ "$1" == --frozen ]] && shift
[[ "$1" == python ]] && shift
exec "$TEST_PYTHON" "$@"
EOF
cat > "$STUBS/npm" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_ROOT/npm.log"
EOF
cat > "$STUBS/npx" <<'EOF'
#!/usr/bin/env bash
mkdir -p dist
for page in index chat voice; do printf 'export\n' > "dist/$page.html"; done
EOF
cat > "$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_ROOT/restarts.log"
EOF
cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s' "${TEST_HTTP_CODE:-200}"
EOF
chmod +x "$STUBS/"*
export TEST_ROOT="$ROOT"
export TEST_PYTHON="${MAGISTRATE_TEST_PYTHON:-$REPO/gateway/.venv/bin/python}"
run_update() {
  PATH="$STUBS:$PATH" MAGISTRATE_DEPLOY_DIR="$DEPLOY" MAGISTRATE_DEPLOY_LOCK="$ROOT/deploy.lock" \
    MAGISTRATE_READINESS_TIMEOUT_SECONDS=1 MAGISTRATE_READINESS_INTERVAL_SECONDS=0 \
    bash "$REPO/scripts/deploy_magistrate.sh"
}
if ! OUTPUT="$(run_update 2>&1)"; then
  printf '%s\n' "$OUTPUT" >&2
  exit 1
fi
grep -Fq 'gateway readiness verified' <<< "$OUTPUT"
! grep -Fq 'synthetic-provider-secret' <<< "$OUTPUT"
[[ "$(cat "$ROOT/restarts.log")" == '--user restart magistrate-gateway.service' ]]
BACKUP="$(find "$ROOT/state/backups" -name '*.sqlite3')"
[[ "$(cat "$BACKUP.commit")" == "$(git -C "$DEPLOY" rev-parse HEAD)" ]]
python3 - "$BACKUP" <<'PY'
import hashlib, json, sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1])
manifest = json.loads(Path(str(p) + '.manifest.json').read_text())
assert hashlib.sha256(p.read_bytes()).hexdigest() == manifest['sha256']
# Preflight/imports must not migrate the live DB before this recovery point.
assert manifest['table_counts'] == {'retained': 1}
with sqlite3.connect(p) as conn:
    assert conn.execute('SELECT id FROM retained').fetchone() == ('legacy-preserved',)
PY
# No retired mode is a deployable fallback, even with otherwise valid secrets.
for setting in MAGISTRATE_LEGACY_CHAT_ENABLED=true MAGISTRATE_PI_OWNERSHIP_ENABLED=true MAGISTRATE_NATIVE_CHAT_ENABLED=false MAGISTRATE_DEV_AUTO_SESSION=true; do
  cp "$DEPLOY/gateway/.env" "$ROOT/env.saved"
  printf '%s\n' "$setting" >> "$DEPLOY/gateway/.env"
  : > "$ROOT/restarts.log"
  if run_update > "$ROOT/output" 2>&1; then echo "unsafe production mode accepted: $setting"; exit 1; fi
  [[ ! -s "$ROOT/restarts.log" ]]
  cp "$ROOT/env.saved" "$DEPLOY/gateway/.env"
done
# PostgreSQL remains supported, with private shared state and snapshot consent.
cp "$DEPLOY/gateway/.env" "$ROOT/sqlite.env"
grep -v '^MAGISTRATE_DB_PATH=' "$ROOT/sqlite.env" > "$DEPLOY/gateway/.env"
printf 'MAGISTRATE_DATABASE_URL=postgresql://test:test@127.0.0.1/magistrate\nMAGISTRATE_STATE_DIR=%s\n' "$ROOT/state" >> "$DEPLOY/gateway/.env"
: > "$ROOT/restarts.log"
if run_update > "$ROOT/output" 2>&1; then echo 'PostgreSQL without backup accepted'; exit 1; fi
grep -Fq 'acknowledged restorable snapshot' "$ROOT/output"
[[ ! -s "$ROOT/restarts.log" ]]
printf 'MAGISTRATE_POSTGRES_BACKUP_CONFIRMED=true\n' >> "$DEPLOY/gateway/.env"
run_update > "$ROOT/output" 2>&1
grep -Fq 'PostgreSQL pre-deploy snapshot acknowledged' "$ROOT/output"
cp "$ROOT/sqlite.env" "$DEPLOY/gateway/.env"
# Native chat can use a configured non-OpenAI catalog provider.
grep -v '^OPENAI_API_KEY=' "$ROOT/sqlite.env" > "$DEPLOY/gateway/.env"
printf 'ANTHROPIC_API_KEY=synthetic-anthropic-secret\n' >> "$DEPLOY/gateway/.env"
run_update > "$ROOT/output" 2>&1
cp "$ROOT/sqlite.env" "$DEPLOY/gateway/.env"
# Domain billing activation is checked before any restart.
printf 'STRIPE_SECRET_KEY=sk_live_synthetic\n' >> "$DEPLOY/gateway/.env"
: > "$ROOT/restarts.log"
if run_update > "$ROOT/output" 2>&1; then echo 'partial billing activation accepted'; exit 1; fi
[[ ! -s "$ROOT/restarts.log" ]]
cp "$ROOT/sqlite.env" "$DEPLOY/gateway/.env"
# 401 and 403 cannot pass a readiness gate.
for code in 401 403 503 000; do
  if TEST_HTTP_CODE="$code" run_update > "$ROOT/output" 2>&1; then echo 'false readiness success'; exit 1; fi
  grep -Fq "last HTTP response $code" "$ROOT/output"
done
printf 'MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true\n' >> "$DEPLOY/gateway/.env"
if run_update > "$ROOT/output" 2>&1; then echo 'missing producer accepted'; exit 1; fi
grep -Fq 'required captain producer needs absolute' "$ROOT/output"
printf 'MAGISTRATE_FIRSTMATE_ROOT=%s\nFM_HOME=%s\n' "$ROOT/code" "$ROOT/home" >> "$DEPLOY/gateway/.env"
run_update >/dev/null
grep -Fxq "verify --root $ROOT/code" "$ROOT/producer.log"
grep -Fxq "ready --root $ROOT/code --fm-home $ROOT/home" "$ROOT/producer.log"
# Refuse dirty/diverged worktrees without destroying local work.
echo retained > "$DEPLOY/local-file"
if run_update > /dev/null 2>&1; then echo 'dirty checkout accepted'; exit 1; fi
[[ -f "$DEPLOY/local-file" ]]
git -C "$DEPLOY" config user.email test@example.invalid; git -C "$DEPLOY" config user.name release-test
git -C "$DEPLOY" add local-file; git -C "$DEPLOY" commit -qm local
LOCAL="$(git -C "$DEPLOY" rev-parse HEAD)"
echo remote > "$SEED/remote-file"; git -C "$SEED" add .; git -C "$SEED" commit -qm remote; git -C "$SEED" push -q
if run_update > /dev/null 2>&1; then echo 'diverged checkout accepted'; exit 1; fi
[[ "$LOCAL" == "$(git -C "$DEPLOY" rev-parse HEAD)" ]]
echo 'deployment safeguards passed'
