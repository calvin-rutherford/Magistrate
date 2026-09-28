"""Offline-friendly SQLite backup/restore and bounded unattached-upload retention.

Restore always writes a NEW destination: never replace a live database or its
WAL. Commands emit counts/checksums, not row contents. Protect and encrypt the
backup volume: SQLite contains plaintext conversations alongside encrypted keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import time


def private_file(path: Path) -> Path:
    if (not path.is_absolute() or path.resolve() != path or not path.is_file()
            or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o600):
        raise ValueError('Source must be an absolute service-owned non-symlink mode-0600 file.')
    return path


def new_private_file(path: Path) -> None:
    parent = path.parent
    if (not path.is_absolute() or parent.resolve() != parent or not parent.is_dir()
            or parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) != 0o700):
        raise ValueError('Destination parent must be an absolute private mode-0700 service-owned directory.')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)


def checksum(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def backup(source: Path, destination: Path) -> dict:
    private_file(source)
    new_private_file(destination)
    manifest_path = Path(str(destination) + '.manifest.json')
    manifest_created = False
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5) as src:
            with sqlite3.connect(destination) as target:
                src.backup(target)
                if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Backup integrity check failed.')
                tables = target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                counts = {name: target.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                          for (name,) in tables}
        with destination.open('rb') as handle:
            os.fsync(handle.fileno())
        manifest = {'schema': 'magistrate.backup.v1', 'created_at': int(time.time()),
                    'sha256': checksum(destination), 'table_counts': counts}
        new_private_file(manifest_path)
        manifest_created = True
        with manifest_path.open('w') as handle:
            json.dump(manifest, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        return manifest
    except Exception:
        destination.unlink(missing_ok=True)
        if manifest_created:
            manifest_path.unlink(missing_ok=True)
        raise


def restore(source: Path, destination: Path) -> dict:
    private_file(source)
    manifest_path = private_file(Path(str(source) + '.manifest.json'))
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'magistrate.backup.v1' or checksum(source) != manifest.get('sha256'):
        raise ValueError('Backup digest does not match its manifest.')
    restored = backup(source, destination)
    if restored['table_counts'] != manifest['table_counts']:
        destination.unlink()
        Path(str(destination) + '.manifest.json').unlink()
        raise ValueError('Restored table counts do not match the backup manifest.')
    return restored


def prune_unattached(source: Path, root: Path, *, before: int, apply: bool = False, limit: int = 100) -> dict:
    """No account deletion or attached-message migration is implied by retention."""
    private_file(source)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError('Upload root must be a normalized non-symlink directory.')
    if not 1 <= limit <= 1000 or before > int(time.time()) - 86400:
        raise ValueError('Retention requires a minimum age of one day and a limit of 1-1000.')
    with sqlite3.connect(source) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(chat_uploads)')}
        # New objects may be retained by perception/artifact references outside
        # message attachments. Never shorten the domain's explicit expiry.
        expiry = ' AND expires_at IS NOT NULL AND expires_at < ?' if 'expires_at' in columns else ''
        params = (before, int(time.time()), limit) if expiry else (before, limit)
        rows = conn.execute('''SELECT * FROM chat_uploads u WHERE created_at < ?'''
            + expiry + ''' AND NOT EXISTS (SELECT 1 FROM chat_message_attachments a WHERE a.upload_id=u.upload_id)
            ORDER BY created_at, upload_id LIMIT ?''', params).fetchall()
        # Validate the whole batch before deleting anything. No DB-supplied path
        # may escape the configured root, even if that row has been corrupted.
        paths = []
        for row in rows:
            key = row['object_key'] if 'object_key' in columns else None
            if key:
                if not re.fullmatch(r'[a-f0-9]{2}/[a-f0-9]{2}/[A-Za-z0-9_-]{24,64}', key):
                    raise ValueError('Unsafe object key in retention batch.')
                path = root / key
            else:
                path = Path(row['path'])
                if path != root / f"{row['upload_id']}-{row['filename']}":
                    raise ValueError('Unsafe upload path in retention batch.')
            if root not in path.parents or path.resolve() != path:
                raise ValueError('Unsafe upload path in retention batch.')
            paths.append(path)
        if apply:
            for row, path in zip(rows, paths):
                path.unlink(missing_ok=True)
                conn.execute('DELETE FROM chat_uploads WHERE upload_id=? AND user_id=?',
                             (row['upload_id'], row['user_id']))
        return {'selected': len(rows), 'deleted': len(rows) if apply else 0, 'dry_run': not apply}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('backup', 'restore'):
        sub = commands.add_parser(command)
        sub.add_argument('source', type=Path)
        sub.add_argument('destination', type=Path)
    prune = commands.add_parser('prune-unattached')
    prune.add_argument('source', type=Path)
    prune.add_argument('root', type=Path)
    prune.add_argument('--before', type=int, required=True)
    prune.add_argument('--limit', type=int, default=100)
    prune.add_argument('--apply', action='store_true')
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        result = {'backup': backup, 'restore': restore, 'prune-unattached': prune_unattached}[command](**args)
    except (OSError, ValueError, sqlite3.Error, KeyError):
        raise SystemExit('Storage operation refused; validate private paths, manifest and schema.') from None
    # Counts/checksums only, including when a filename is hostile.
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
