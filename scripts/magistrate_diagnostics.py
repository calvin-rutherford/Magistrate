#!/usr/bin/env python3
"""Content-free, read-only SQLite diagnostics. Never import Gateway startup.

Requires an explicit existing DB and owner. For WAL databases use an authorized
consistent snapshot: immutable reads deliberately refuse an outstanding WAL.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('schema_migrations', 'gateway_sessions', 'magi_objective_submissions',
          'firstmate_execution_objectives', 'firstmate_decisions', 'push_tokens',
          'notification_push_deliveries', 'personal_resource_reservations',
          'objective_state_projections')
REQUIRED = frozenset(TABLES[:5])
ERRORS = {
    'personal_runtime_limits_unavailable': 'The pinned runtime lacks enforced personal limits; do not enable dispatch.',
    'objective_contract_invalid': 'Inspect the frozen contract through an authorized owner view.',
    'objective_dispatch_runtime-unavailable': 'Verify the pinned runtime configuration without retrying uncertain work.',
    'objective_dispatch_runtime-bound-exceeded': 'Dispatch is uncertain; reconcile the deterministic task before retrying.',
    'objective_dispatch_intake-wake-failed': 'Queue publication may have succeeded; retain capacity and reconcile.',
    'objective_insufficient_credits': 'Commercial admission refused; review the authenticated billing view.',
}


def inspect(db_path: Path, owner: str) -> dict:
    if not db_path.is_absolute() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', owner):
        raise ValueError('explicit_absolute_database_and_owner_required')
    if any(path.is_symlink() for path in (db_path, *db_path.parents)) or not db_path.is_file():
        raise ValueError('existing_regular_database_required')
    with db_path.open('rb') as stream:
        if stream.read(16) != b'SQLite format 3\x00':
            raise ValueError('wrong_database_format')
    before = db_path.stat()
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.is_symlink() or (sidecar.exists() and sidecar.stat().st_size):
            raise ValueError('consistent_snapshot_required')
    # immutable prevents SQLite from creating/updating sidecars. Never use this
    # against uncheckpointed WAL: those facts would silently disappear.
    connection = sqlite3.connect(db_path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED.issubset(tables):
            raise ValueError('wrong_gateway_database')
        schema = connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        expected = next(node.value.value for node in ast.parse((ROOT / 'gateway/app/db.py').read_text()).body
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SCHEMA_VERSION' for t in node.targets))
        now = int(time.time())
        active = connection.execute('SELECT COUNT(*) FROM gateway_sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?', (owner, now)).fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM firstmate_decisions WHERE owner_user_id=? AND state IN ('pending','answering')", (owner,)).fetchone()[0]
        errors = []
        for code, updated in connection.execute('''SELECT last_error_code,updated_at FROM magi_objective_submissions
                WHERE owner_user_id=? AND last_error_code IS NOT NULL ORDER BY updated_at DESC LIMIT 5''', (owner,)):
            safe = code if code in ERRORS else 'redacted_task_error'
            errors.append({'code': safe, 'updated_at_ms': updated,
                           'recovery': ERRORS.get(safe, 'Review the authenticated task view; do not replay uncertain dispatch.')})
        registered = connection.execute('SELECT COUNT(*) FROM push_tokens WHERE user_id=? AND revoked_at IS NULL', (owner,)).fetchone()[0] if 'push_tokens' in tables else None
        states = dict(connection.execute('SELECT state,COUNT(*) FROM notification_push_deliveries WHERE user_id=? GROUP BY state', (owner,))) if 'notification_push_deliveries' in tables else {}
        try:
            revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], check=True,
                                      capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = 'unavailable'
        after = db_path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('database_changed_during_observation')
        return {
            'revision': revision if re.fullmatch('[0-9a-f]{40,64}', revision) else 'unavailable',
            'database': {'selected_path': str(db_path), 'read_only': True, 'schema_version': schema,
                         'expected_schema_version': expected, 'schema_presence': {name: name in tables for name in TABLES}},
            'auth': {'unexpired_unrevoked_bearer_rows': active, 'provider_refresh_health': 'not_checked',
                     'live_authentication': 'not_checked'},
            'runtime': {'selected': 'firstmate', 'selection_basis': 'candidate_default_not_live_configuration',
                        'process_health': 'not_observed', 'personal_limit_enforcement': 'unavailable_in_reviewed_pin'},
            'pending_decisions': pending, 'recent_task_errors': errors,
            'push_configuration': {'registered_devices': registered,
                'configuration_scope': 'diagnostic_process_only',
                'eas_project_setting_present': bool(os.getenv('EXPO_PUBLIC_EAS_PROJECT_ID')),
                'apns_signing': 'not_observed'},
            'push_evidence': {'queued': states.get('sending', 0) + states.get('retry', 0),
                'provider_accepted': states.get('pending', 0), 'provider_handoff': states.get('delivered', 0),
                'failed': states.get('failed', 0), 'expired': states.get('expired', 0),
                'observed_phone_delivery': 'unknown', 'owner_saw_notification': 'unknown'},
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path, help='Exact existing SQLite file; no fallback or creation')
    parser.add_argument('--owner', required=True, help='Exact authenticated principal ID, used only to scope reads')
    args = parser.parse_args()
    try:
        result = inspect(args.db, args.owner)
    except (ValueError, OSError, sqlite3.Error):
        # Never echo driver errors, input paths, credentials, or row contents on failure.
        print(json.dumps({'status': 'refused', 'reason': 'Require an existing regular Gateway SQLite snapshot with no outstanding WAL/journal; no database was created.'}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
