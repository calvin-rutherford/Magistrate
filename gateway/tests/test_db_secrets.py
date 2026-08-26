import base64
import sqlite3

import pytest
from cryptography.fernet import Fernet

from app import db


def _legacy_encrypt(value: str, legacy_key: str) -> str:
    raw_key = legacy_key.encode('utf-8')
    old_derived_key = base64.urlsafe_b64encode(raw_key.ljust(32, b'\0')[:32])
    return Fernet(old_derived_key).encrypt(value.encode('utf-8')).decode('ascii')


def _configure_current(monkeypatch, *, version='v1'):
    monkeypatch.setenv('MAGISTRATE_ENV', 'test')
    monkeypatch.setenv('MAGISTRATE_SECRET_KEY', Fernet.generate_key().decode('ascii'))
    monkeypatch.setenv('MAGISTRATE_SECRET_KEY_VERSION', version)
    for name in (
        'MAGISTRATE_PREVIOUS_SECRET_KEY',
        'MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION',
        'MAGISTRATE_KEY_ROTATION_ENABLED',
        'MAGISTRATE_LEGACY_SECRET_KEY',
        'MAGISTRATE_ALLOW_LEGACY_MIGRATION',
    ):
        monkeypatch.delenv(name, raising=False)


def test_production_requires_an_explicit_key(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.delenv('MAGISTRATE_SECRET_KEY', raising=False)

    with pytest.raises(db.SecretConfigurationError, match='MAGISTRATE_SECRET_KEY'):
        db.validate_secret_configuration()


@pytest.mark.parametrize('bad_key', ['', 'not-a-fernet-key', 'magistrate_super_secret_fernet_key_32bytes_len='])
def test_production_rejects_missing_or_unsuitable_keys(monkeypatch, bad_key):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.setenv('MAGISTRATE_SECRET_KEY', bad_key)

    with pytest.raises(db.SecretConfigurationError):
        db.validate_secret_configuration()


def test_production_rejects_whitespace_padded_key(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.setenv(
        'MAGISTRATE_SECRET_KEY', f' {Fernet.generate_key().decode("ascii")} '
    )

    with pytest.raises(db.SecretConfigurationError):
        db.validate_secret_configuration()


def test_encryption_is_versioned_and_tampering_fails_closed(monkeypatch):
    _configure_current(monkeypatch)

    encrypted = db.encrypt_token('oauth-secret')
    assert encrypted.startswith('v1:')
    assert db.decrypt_token(encrypted) == 'oauth-secret'

    tampered = f'{encrypted[:-1]}{"A" if encrypted[-1] != "A" else "B"}'
    with pytest.raises(db.SecretDecryptionError):
        db.decrypt_token(tampered)
    with pytest.raises(db.SecretDecryptionError):
        db.decrypt_token('oauth-secret')


def test_legacy_migration_requires_explicit_opt_in_and_reencrypts(monkeypatch):
    _configure_current(monkeypatch)
    legacy_key = 'legacy-only-secret-material'
    legacy_ciphertext = _legacy_encrypt('legacy-oauth-secret', legacy_key)

    with pytest.raises(db.SecretMigrationError):
        db.migrate_legacy_ciphertext(legacy_ciphertext, legacy_key=legacy_key)

    migrated = db.migrate_legacy_ciphertext(
        legacy_ciphertext, legacy_key=legacy_key, allow_legacy=True
    )
    assert migrated.startswith('v1:')
    assert migrated != legacy_ciphertext
    assert db.decrypt_token(migrated) == 'legacy-oauth-secret'
    with pytest.raises(db.SecretDecryptionError):
        db.decrypt_token(legacy_ciphertext)


def test_rotation_rewrites_only_configured_previous_version(monkeypatch):
    old_key = Fernet.generate_key().decode('ascii')
    old_payload = Fernet(old_key.encode()).encrypt(b'rotated-secret').decode('ascii')
    old_ciphertext = f'v1:{old_payload}'
    _configure_current(monkeypatch, version='v2')
    monkeypatch.setenv('MAGISTRATE_PREVIOUS_SECRET_KEY', old_key)
    monkeypatch.setenv('MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION', 'v1')
    monkeypatch.setenv('MAGISTRATE_KEY_ROTATION_ENABLED', 'true')

    rotated = db.rotate_encrypted_token(old_ciphertext)
    assert rotated.startswith('v2:')
    assert db.decrypt_token(rotated) == 'rotated-secret'

    with pytest.raises(db.SecretRotationError):
        db.rotate_encrypted_token('v3:not-configured')


def test_rotation_rejects_tampering_and_plaintext_without_writing(monkeypatch):
    _configure_current(monkeypatch)
    encrypted = db.encrypt_token('persistent-secret')
    with pytest.raises(db.SecretRotationError):
        db.rotate_encrypted_token('persistent-secret')

    tampered = f'{encrypted[:-1]}{"A" if encrypted[-1] != "A" else "B"}'
    with pytest.raises(db.SecretRotationError):
        db.rotate_encrypted_token(tampered)


def test_persistence_rewrite_is_bounded_atomic_and_does_not_log_secrets(
    monkeypatch, capsys, tmp_path
):
    _configure_current(monkeypatch)
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'credentials.sqlite3'))
    db_path = db.DB_PATH
    db.upsert_connected_account(
        'secret-test-user', 'github', 'secret-user', access_token='persisted-secret'
    )
    report = db.rotate_oauth_credentials(limit=10, apply=False)
    assert report.scanned >= 1
    assert report.rewritten == 0
    assert 'persisted-secret' not in capsys.readouterr().out

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            'SELECT access_token_enc FROM oauth_credentials WHERE id = ?',
            ('cred_secret-test-user_github',),
        ).fetchone()[0]
    assert stored.startswith('v1:')
    assert db.decrypt_token(stored) == 'persisted-secret'


def test_legacy_persistence_migration_is_atomic(monkeypatch, tmp_path):
    _configure_current(monkeypatch)
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'legacy.sqlite3'))
    legacy_key = 'legacy-persistence-key'
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO connected_accounts "
            "(id, user_id, provider, provider_user_id, provider_username, status, scopes, created_at, updated_at) "
            "VALUES ('legacy_user_github', 'legacy_user', 'github', 'legacy', 'legacy', 'connected', '', 1, 1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO oauth_credentials "
            "(id, connected_account_id, access_token_enc, refresh_token_enc, expires_at) "
            "VALUES ('cred_legacy_user_github', 'legacy_user_github', ?, '', 1)",
            (_legacy_encrypt('legacy-persisted-secret', legacy_key),),
        )

    report = db.migrate_legacy_oauth_credentials(
        legacy_key=legacy_key, allow_legacy=True, limit=10, apply=True
    )
    assert report.rewritten == 1
    with sqlite3.connect(db.DB_PATH) as conn:
        migrated = conn.execute(
            'SELECT access_token_enc FROM oauth_credentials WHERE id = ?',
            ('cred_legacy_user_github',),
        ).fetchone()[0]
    assert migrated.startswith('v1:')
    assert db.decrypt_token(migrated) == 'legacy-persisted-secret'


def test_legacy_persistence_migration_rolls_back_on_plaintext(monkeypatch, tmp_path):
    _configure_current(monkeypatch)
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'rollback.sqlite3'))
    legacy_key = 'legacy-rollback-key'
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO connected_accounts "
            "(id, user_id, provider, provider_user_id, provider_username, status, scopes, created_at, updated_at) "
            "VALUES ('rollback_user_github', 'rollback_user', 'github', 'rollback', 'rollback', 'connected', '', 1, 1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO oauth_credentials "
            "(id, connected_account_id, access_token_enc, refresh_token_enc, expires_at) "
            "VALUES ('cred_rollback_user_github', 'rollback_user_github', ?, '', 1)",
            (_legacy_encrypt('must-remain-legacy', legacy_key),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO oauth_credentials "
            "(id, connected_account_id, access_token_enc, refresh_token_enc, expires_at) "
            "VALUES ('cred_rollback_user_twitter', 'rollback_user_github', 'plaintext-not-allowed', '', 1)"
        )

    with pytest.raises(db.SecretMigrationError):
        db.migrate_legacy_oauth_credentials(
            legacy_key=legacy_key, allow_legacy=True, limit=10, apply=True
        )
    with sqlite3.connect(db.DB_PATH) as conn:
        stored = conn.execute(
            'SELECT access_token_enc FROM oauth_credentials WHERE id = ?',
            ('cred_rollback_user_github',),
        ).fetchone()[0]
    assert not stored.startswith('v1:')
