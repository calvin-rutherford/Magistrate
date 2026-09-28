import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from scripts.production_preflight import load_service_environment
from scripts.storage_ops import backup, restore, prune_unattached


def database(tmp_path):
    path = tmp_path / 'source.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE secrets(value TEXT)')
        conn.execute("INSERT INTO secrets VALUES ('private-content')")
    path.chmod(0o600)
    return path


def test_online_backup_and_restore_to_new_file_are_integrity_checked(tmp_path):
    source = database(tmp_path)
    snapshot = tmp_path / 'backup.sqlite3'
    manifest = backup(source, snapshot)
    assert manifest['table_counts'] == {'secrets': 1}
    assert 'private-content' not in json.dumps(manifest)
    assert snapshot.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / 'restored.sqlite3'
    assert restore(snapshot, restored)['table_counts'] == manifest['table_counts']
    with sqlite3.connect(restored) as conn:
        assert conn.execute('SELECT value FROM secrets').fetchone() == ('private-content',)
    # Never replace a live DB, an existing backup, or a symlink target.
    with pytest.raises(FileExistsError):
        restore(snapshot, source)
    with pytest.raises(FileExistsError):
        backup(source, snapshot)
    symlink = tmp_path / 'alias.sqlite3'
    symlink.symlink_to(source)
    with pytest.raises(ValueError):
        backup(symlink, tmp_path / 'unsafe.sqlite3')


def test_corrupt_backup_cannot_restore(tmp_path):
    source = database(tmp_path)
    snapshot = tmp_path / 'backup.sqlite3'
    backup(source, snapshot)
    with snapshot.open('ab') as handle:
        handle.write(b'tampered')
    with pytest.raises(ValueError, match='digest'):
        restore(snapshot, tmp_path / 'restore.sqlite3')
    assert not (tmp_path / 'restore.sqlite3').exists()


def test_retention_is_dry_run_bounded_and_does_not_remove_attached_uploads(tmp_path):
    source = database(tmp_path)
    root = tmp_path / 'uploads'
    root.mkdir(mode=0o700)
    before = int(time.time()) - 2 * 86400
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE chat_uploads(upload_id TEXT, user_id TEXT, filename TEXT, path TEXT, created_at INTEGER)')
        conn.execute('CREATE TABLE chat_message_attachments(upload_id TEXT)')
        for index in range(3):
            path = root / f'upload{index}-file.txt'
            path.write_text('data')
            conn.execute('INSERT INTO chat_uploads VALUES(?,?,?,?,?)',
                         (f'upload{index}', f'owner{index}', 'file.txt', str(path), before - 100))
        conn.execute("INSERT INTO chat_message_attachments VALUES ('upload2')")
    assert prune_unattached(source, root, before=before)['selected'] == 2
    assert len(list(root.iterdir())) == 3
    assert prune_unattached(source, root, before=before, apply=True, limit=1)['deleted'] == 1
    assert len(list(root.iterdir())) == 2
    assert prune_unattached(source, root, before=before, apply=True)['deleted'] == 1
    assert (root / 'upload2-file.txt').is_file()
    with pytest.raises(ValueError):
        prune_unattached(source, root, before=int(time.time()), apply=True)


def test_retention_refuses_path_traversal_before_deleting_anything(tmp_path):
    source = database(tmp_path)
    root = tmp_path / 'uploads'
    root.mkdir(mode=0o700)
    outside = tmp_path / 'credential'
    outside.write_text('keep')
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE chat_uploads(upload_id TEXT, user_id TEXT, filename TEXT, path TEXT, created_at INTEGER)')
        conn.execute('CREATE TABLE chat_message_attachments(upload_id TEXT)')
        conn.execute('INSERT INTO chat_uploads VALUES(?,?,?,?,?)', ('id', 'owner', '../../credential', str(outside), 0))
    with pytest.raises(ValueError):
        prune_unattached(source, root, before=int(time.time()) - 86400, apply=True)
    assert outside.read_text() == 'keep'


def test_retention_respects_object_keys_and_extended_domain_expiry(tmp_path):
    source = database(tmp_path)
    root = tmp_path / 'objects'
    leaf = root / 'ab' / 'cd'
    leaf.mkdir(parents=True, mode=0o700)
    now = int(time.time())
    with sqlite3.connect(source) as conn:
        conn.execute('''CREATE TABLE chat_uploads(upload_id TEXT, user_id TEXT, filename TEXT,
            path TEXT, created_at INTEGER, object_key TEXT, expires_at INTEGER)''')
        conn.execute('CREATE TABLE chat_message_attachments(upload_id TEXT)')
        for index, expiry in enumerate((now - 1, now + 86400)):
            key = 'ab/cd/' + str(index) * 24
            (root / key).write_text('private')
            conn.execute('INSERT INTO chat_uploads VALUES(?,?,?,?,?,?,?)',
                         (str(index), 'owner', 'file.txt', '', now - 3 * 86400, key, expiry))
    result = prune_unattached(source, root, before=now - 86400, apply=True)
    assert result['deleted'] == 1
    assert not (leaf / ('0' * 24)).exists()
    assert (leaf / ('1' * 24)).exists()


def test_importing_database_configuration_never_migrates_or_connects(tmp_path):
    import subprocess
    import sys
    environment = {**os.environ, 'MAGISTRATE_ENV': 'test',
                   'MAGISTRATE_DB_PATH': str(tmp_path / 'must-not-exist.sqlite3')}
    environment.pop('MAGISTRATE_DATABASE_URL', None)
    subprocess.run([sys.executable, '-c', 'from app.db import validate_secret_configuration; validate_secret_configuration()'],
                   env=environment, check=True, capture_output=True)
    assert not (tmp_path / 'must-not-exist.sqlite3').exists()
    environment.update(MAGISTRATE_DATABASE_URL='postgresql://unused:unused@127.0.0.1:1/unused',
                       MAGISTRATE_STATE_DIR=str(tmp_path))
    environment.pop('MAGISTRATE_DB_PATH')
    subprocess.run([sys.executable, '-c', 'from app.db import validate_secret_configuration; validate_secret_configuration()'],
                   env=environment, check=True, capture_output=True)


def test_service_environment_parser_does_not_evaluate_shell(tmp_path):
    path = tmp_path / 'service.env'
    path.write_text("MAGISTRATE_ENV=production\nLITERAL='$(touch /tmp/never-run)'\n")
    path.chmod(0o600)
    assert load_service_environment(path)['LITERAL'] == '$(touch /tmp/never-run)'
    path.write_text('KEY=a\nKEY=b\n')
    with pytest.raises(ValueError, match='duplicate'):
        load_service_environment(path)
    path.write_text('KEY=secret\n')
    path.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        load_service_environment(path)
