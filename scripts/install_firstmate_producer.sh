#!/usr/bin/env bash
set -euo pipefail
umask 077

# Install and activate the reviewed Firstmate captain-event producer without
# modifying an operator's unmanaged FM_HOME checkout. The immutable code root
# and runtime-private home remain deliberately separate.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$PROJECT_ROOT/gateway${PYTHONPATH:+:$PYTHONPATH}"
eval "$(python3 - <<'PY'
import shlex
from app.firstmate_producer import FIRSTMATE_PRODUCER_PIN as pin
for name, value in {
    'PIN_SOURCE': pin.source,
    'PIN_COMMIT': pin.commit,
    'PIN_TREE': pin.tree,
}.items():
    print(f'{name}={shlex.quote(value)}')
PY
)"
DEFAULT_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/magistrate/firstmate-producer/$PIN_COMMIT"

usage() {
  cat <<EOF
Usage:
  $0 install [--root ABSOLUTE_PATH]
  $0 verify [--root ABSOLUTE_PATH]
  $0 activate --fm-home ABSOLUTE_PATH [--root ABSOLUTE_PATH] --sessions-reloaded
  $0 ready --fm-home ABSOLUTE_PATH [--root ABSOLUTE_PATH]
  $0 deactivate --fm-home ABSOLUTE_PATH [--root ABSOLUTE_PATH]

The source and commit are locked by runtime/firstmate-producer.lock.json.
Activation is separate and requires an explicit assertion that applicable Pi
sessions loaded the pinned code. No command edits the FM_HOME Git checkout.
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

trusted_git() {
  env \
    -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR -u GIT_INDEX_FILE \
    -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_ATTR_NOSYSTEM=1 \
    GIT_TERMINAL_PROMPT=0 \
    git -c core.hooksPath=/dev/null "$@"
}

COMMAND="${1:-}"
[[ -n "$COMMAND" ]] || { usage >&2; exit 2; }
shift
RUNTIME_ROOT="$DEFAULT_ROOT"
FM_HOME_ARG=""
SESSIONS_RELOADED=0
while (($#)); do
  case "$1" in
    --root)
      (($# >= 2)) || die '--root requires a value'
      RUNTIME_ROOT=$2
      shift 2
      ;;
    --fm-home)
      (($# >= 2)) || die '--fm-home requires a value'
      FM_HOME_ARG=$2
      shift 2
      ;;
    --sessions-reloaded)
      SESSIONS_RELOADED=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$RUNTIME_ROOT" = /* ]] || die '--root must be an absolute path'

validate_contract_files() {
  local root=$1 home=${2:-}
  python3 - "$root" "$home" <<'PY'
import sys
from app.firstmate_producer import validate_producer_root
validate_producer_root(sys.argv[1], fm_home=sys.argv[2] or None)
PY
}

verify_root() {
  local root=$1 actual_commit actual_tree source status
  [[ -d "$root" && ! -L "$root" ]] || die 'pinned Firstmate root is unavailable'
  # Validate owned metadata and executable hashes before asking Git to inspect
  # the checkout, then run Git without ambient repository/config/hook controls.
  validate_contract_files "$root"
  actual_commit=$(trusted_git -C "$root" rev-parse --verify 'HEAD^{commit}' 2>/dev/null) \
    || die 'pinned Firstmate root is not a Git checkout'
  [[ "$actual_commit" == "$PIN_COMMIT" ]] || die 'pinned Firstmate root has the wrong commit'
  actual_tree=$(trusted_git -C "$root" rev-parse --verify 'HEAD^{tree}' 2>/dev/null) \
    || die 'pinned Firstmate root has no verifiable tree'
  [[ "$actual_tree" == "$PIN_TREE" ]] || die 'pinned Firstmate root has the wrong tree'
  source=$(trusted_git -C "$root" config --get remote.origin.url 2>/dev/null || true)
  [[ "$source" == "$PIN_SOURCE" ]] || die 'pinned Firstmate root has an unapproved source URL'
  status=$(trusted_git -C "$root" status --porcelain=v1 --untracked-files=all 2>/dev/null) \
    || die 'pinned Firstmate root status is unavailable'
  [[ -z "$status" ]] || die 'pinned Firstmate root is modified'
}

validate_install_parent() {
  python3 - "$1" <<'PY'
import os, stat, sys
path = os.path.normpath(sys.argv[1])
if not os.path.isabs(path) or path == os.sep:
    raise SystemExit('error: install parent must be an absolute non-root directory')
current = os.sep
for component in path.split(os.sep)[1:]:
    current = os.path.join(current, component)
    info = os.lstat(current)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, os.geteuid()}:
        raise SystemExit('error: install parent ownership chain is untrusted')
    if info.st_mode & stat.S_IWOTH and not (current != path and info.st_mode & stat.S_ISVTX):
        raise SystemExit('error: install parent ownership chain is world-writable')
PY
}

atomic_flag() {
  local operation=$1 home=$2
  python3 - "$operation" "$home" <<'PY'
import os, stat, sys, uuid
operation, home = sys.argv[1:]
config = os.path.join(home, 'config')
try:
    info = os.lstat(config)
except FileNotFoundError:
    os.mkdir(config, 0o700)
    info = os.lstat(config)
if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & stat.S_IWOTH:
    raise SystemExit('error: FM_HOME config directory is untrusted')
flag = os.path.join(config, 'captain-event-outbox')
if operation == 'enable':
    try:
        existing = os.lstat(flag)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        descriptor = os.open(flag, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            opened = os.fstat(descriptor)
            content = os.read(descriptor, 32)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(existing.st_mode) or stat.S_ISLNK(existing.st_mode)
            or existing.st_nlink != 1 or existing.st_uid != os.geteuid()
            or existing.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or (existing.st_dev, existing.st_ino) != (opened.st_dev, opened.st_ino)
            or content != b'enabled\n'
        ):
            raise SystemExit('error: activation flag is malformed or untrusted')
        print('existing')
        raise SystemExit(0)
    temporary = os.path.join(config, f'.captain-event-outbox.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b'enabled\n')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, flag)
    directory = os.open(config, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    print('created')
elif operation == 'disable':
    try:
        info = os.lstat(flag)
    except FileNotFoundError:
        raise SystemExit(0)
    descriptor = os.open(flag, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        content = os.read(descriptor, 32)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1 or info.st_uid != os.geteuid()
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
        or content != b'enabled\n'
    ):
        raise SystemExit('error: activation flag is malformed or untrusted')
    os.unlink(flag)
    directory = os.open(config, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
else:
    raise SystemExit('error: unknown activation operation')
PY
}

run_outbox() {
  local root=$1 home=$2
  shift 2
  env -i \
    FM_HOME="$home" FM_ROOT_OVERRIDE="$root" \
    HOME="$(dirname "$home")" PATH=/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    "$root/bin/fm-captain-event.sh" "$@"
}

require_home() {
  [[ -n "$FM_HOME_ARG" && "$FM_HOME_ARG" = /* ]] || die '--fm-home must be an absolute path'
  [[ -d "$FM_HOME_ARG" && ! -L "$FM_HOME_ARG" ]] || die 'FM_HOME is unavailable'
  validate_contract_files "$RUNTIME_ROOT" "$FM_HOME_ARG"
}

case "$COMMAND" in
  install)
    [[ -z "$FM_HOME_ARG" && "$SESSIONS_RELOADED" -eq 0 ]] || die 'install accepts only --root'
    if [[ -e "$RUNTIME_ROOT" || -L "$RUNTIME_ROOT" ]]; then
      verify_root "$RUNTIME_ROOT"
      printf 'Firstmate producer already installed: commit=%s\n' "$PIN_COMMIT"
      exit 0
    fi
    parent=$(dirname "$RUNTIME_ROOT")
    mkdir -p "$parent"
    validate_install_parent "$parent"
    stage=$(mktemp -d "$parent/.firstmate-producer.${PIN_COMMIT}.XXXXXXXX")
    cleanup_stage() { [[ -z "${stage:-}" ]] || rm -rf -- "$stage"; }
    trap cleanup_stage EXIT INT TERM
    trusted_git -C "$stage" init --quiet --template=
    trusted_git -C "$stage" remote add origin "$PIN_SOURCE"
    trusted_git -C "$stage" fetch --quiet --no-tags --depth=1 origin "$PIN_COMMIT"
    [[ "$(trusted_git -C "$stage" rev-parse FETCH_HEAD)" == "$PIN_COMMIT" ]] \
      || die 'allowed Firstmate source did not return the locked commit'
    trusted_git -C "$stage" checkout --quiet --detach "$PIN_COMMIT"
    verify_root "$stage"
    python3 - "$stage" "$RUNTIME_ROOT" <<'PY'
import os, sys
os.rename(sys.argv[1], sys.argv[2])
parent = os.open(os.path.dirname(sys.argv[2]), os.O_RDONLY)
try: os.fsync(parent)
finally: os.close(parent)
PY
    stage=
    trap - EXIT INT TERM
    printf 'Installed Firstmate producer: commit=%s\n' "$PIN_COMMIT"
    ;;
  verify)
    [[ -z "$FM_HOME_ARG" && "$SESSIONS_RELOADED" -eq 0 ]] || die 'verify accepts only --root'
    verify_root "$RUNTIME_ROOT"
    printf 'Verified Firstmate producer: commit=%s\n' "$PIN_COMMIT"
    ;;
  activate)
    [[ "$SESSIONS_RELOADED" -eq 1 ]] \
      || die 'activation requires --sessions-reloaded after every applicable Pi session loaded the pin'
    verify_root "$RUNTIME_ROOT"
    require_home
    activation_change=$(atomic_flag enable "$FM_HOME_ARG")
    if ! run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" recover >/dev/null \
       || ! tail=$(run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" validate); then
      if [[ "$activation_change" == created ]]; then
        atomic_flag disable "$FM_HOME_ARG" \
          || die 'producer state validation failed and activation rollback could not be completed'
        die 'producer state validation failed; newly created activation was rolled back'
      fi
      die 'producer state validation failed; existing activation was left unchanged'
    fi
    if [[ ! "$tail" =~ ^(0|[1-9][0-9]{0,4})$ || "$tail" -gt 10000 ]]; then
      if [[ "$activation_change" == created ]]; then
        atomic_flag disable "$FM_HOME_ARG" \
          || die 'producer returned an invalid tail and activation rollback could not be completed'
        die 'producer returned an invalid tail; newly created activation was rolled back'
      fi
      die 'producer returned an invalid tail; existing activation was left unchanged'
    fi
    printf 'Activated Firstmate producer: commit=%s tail=%s\n' "$PIN_COMMIT" "$tail"
    ;;
  ready)
    [[ "$SESSIONS_RELOADED" -eq 0 ]] || die 'ready does not accept --sessions-reloaded'
    verify_root "$RUNTIME_ROOT"
    require_home
    run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" enabled >/dev/null \
      || die 'Firstmate producer is not activated'
    tail=$(run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" validate) \
      || die 'Firstmate producer state is invalid'
    [[ "$tail" =~ ^(0|[1-9][0-9]{0,4})$ && "$tail" -le 10000 ]] \
      || die 'Firstmate producer returned an invalid tail'
    printf 'Firstmate producer ready: commit=%s tail=%s\n' "$PIN_COMMIT" "$tail"
    ;;
  deactivate)
    [[ "$SESSIONS_RELOADED" -eq 0 ]] || die 'deactivate does not accept --sessions-reloaded'
    verify_root "$RUNTIME_ROOT"
    require_home
    if run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" enabled >/dev/null 2>&1; then
      run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" recover >/dev/null \
        || die 'producer recovery failed; activation was left unchanged'
      run_outbox "$RUNTIME_ROOT" "$FM_HOME_ARG" validate >/dev/null \
        || die 'producer validation failed; activation was left unchanged'
    fi
    atomic_flag disable "$FM_HOME_ARG"
    printf 'Deactivated Firstmate producer; durable outbox state was retained.\n'
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
