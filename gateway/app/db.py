import os
import sqlite3
import base64
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Dict, Any, List, Optional, Tuple
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

# Deployments must provide an absolute path outside the checkout so upgrades
# cannot replace or strand the operator's SQLite state. Development and tests
# get a worktree-local default; production init_db() fails closed if the path
# was omitted.
_environment_at_import = os.getenv('MAGISTRATE_ENV', '').strip().lower()
_configured_db_path = os.getenv('MAGISTRATE_DB_PATH', '').strip()
DEFAULT_CIPHERTEXT_VERSION = 'v1'
DEVELOPMENT_MODES = frozenset({'dev', 'development', 'test', 'testing'})
DB_PATH = _configured_db_path
if not DB_PATH and _environment_at_import in DEVELOPMENT_MODES:
    DB_PATH = str(Path(__file__).resolve().parents[1] / 'magistrate.db')
LEGACY_MIGRATION_FLAG = 'MAGISTRATE_ALLOW_LEGACY_MIGRATION'
ROTATION_FLAG = 'MAGISTRATE_KEY_ROTATION_ENABLED'
MAX_ROTATION_ROWS = 1_000


class SecretConfigurationError(RuntimeError):
    """Raised when credential encryption is not safely configured."""


class SecretDecryptionError(RuntimeError):
    """Raised when an encrypted credential cannot be authenticated."""


class SecretMigrationError(RuntimeError):
    """Raised when an explicitly requested legacy migration cannot proceed."""


class SecretRotationError(RuntimeError):
    """Raised when a bounded credential rotation cannot proceed."""


@dataclass(frozen=True)
class _SecretSettings:
    key_material: str
    fernet: Fernet
    version: str
    previous_fernet: Optional[Fernet]
    previous_version: Optional[str]
    rotation_enabled: bool


_EPHEMERAL_TEST_KEY = Fernet.generate_key().decode('ascii')


def _is_truthy(value: Optional[str]) -> bool:
    return (value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _environment() -> str:
    return os.getenv('MAGISTRATE_ENV', '').strip().lower()


def _validate_version(value: str, setting_name: str) -> str:
    version = value.strip()
    if not version.startswith('v') or not version[1:].isdigit() or int(version[1:]) < 1:
        raise SecretConfigurationError(f'{setting_name} must be a positive version such as v1')
    return version


def _fernet_from_key(key_material: str, setting_name: str) -> Fernet:
    key = key_material
    # Fernet.generate_key() produces a 44-character URL-safe base64 key. Do
    # not normalize, pad, truncate, or otherwise make weak configuration work.
    if len(key) != 44 or any(char.isspace() for char in key):
        raise SecretConfigurationError(f'{setting_name} must be a generated Fernet key')
    try:
        return Fernet(key.encode('ascii'))
    except (UnicodeEncodeError, ValueError, TypeError) as exc:
        raise SecretConfigurationError(f'{setting_name} must be a generated Fernet key') from exc


def _load_secret_settings() -> _SecretSettings:
    environment = _environment()
    key_material = os.getenv('MAGISTRATE_SECRET_KEY', '')
    if not key_material:
        if environment in DEVELOPMENT_MODES:
            key_material = _EPHEMERAL_TEST_KEY
        else:
            raise SecretConfigurationError(
                'MAGISTRATE_SECRET_KEY is required outside explicit development/test mode'
            )
    current_version = _validate_version(
        os.getenv('MAGISTRATE_SECRET_KEY_VERSION', DEFAULT_CIPHERTEXT_VERSION),
        'MAGISTRATE_SECRET_KEY_VERSION',
    )
    current_fernet = _fernet_from_key(key_material, 'MAGISTRATE_SECRET_KEY')

    previous_key = os.getenv('MAGISTRATE_PREVIOUS_SECRET_KEY', '')
    previous_version = os.getenv('MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION', '') or None
    previous_fernet = None
    if previous_key or previous_version:
        if not previous_key or not previous_version:
            raise SecretConfigurationError(
                'MAGISTRATE_PREVIOUS_SECRET_KEY and MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION must be set together'
            )
        previous_version = _validate_version(
            previous_version, 'MAGISTRATE_PREVIOUS_SECRET_KEY_VERSION'
        )
        if previous_version == current_version:
            raise SecretConfigurationError('previous and current secret versions must differ')
        previous_fernet = _fernet_from_key(
            previous_key, 'MAGISTRATE_PREVIOUS_SECRET_KEY'
        )

    rotation_enabled = _is_truthy(os.getenv(ROTATION_FLAG))
    if rotation_enabled and previous_fernet is None:
        raise SecretConfigurationError(
            f'{ROTATION_FLAG}=true requires a previous secret key and version'
        )

    return _SecretSettings(
        key_material=key_material,
        fernet=current_fernet,
        version=current_version,
        previous_fernet=previous_fernet,
        previous_version=previous_version,
        rotation_enabled=rotation_enabled,
    )


def validate_secret_configuration() -> None:
    """Validate the encryption contract at process startup or before persistence."""
    _load_secret_settings()


def _get_fernet() -> Fernet:
    return _load_secret_settings().fernet


def _encrypt_with(fernet: Fernet, version: str, plain_token: str) -> str:
    return f'{version}:{fernet.encrypt(plain_token.encode("utf-8")).decode("ascii")}'


def encrypt_token(plain_token: str) -> str:
    if not plain_token:
        return ''
    settings = _load_secret_settings()
    return _encrypt_with(settings.fernet, settings.version, plain_token)


def _split_ciphertext(
    cipher_token: str, error_type: type[Exception] = SecretDecryptionError
) -> Tuple[str, str]:
    if not isinstance(cipher_token, str):
        raise error_type('Encrypted credential has an invalid format')
    version, separator, payload = cipher_token.partition(':')
    if not separator or not payload or not version.startswith('v') or not version[1:].isdigit():
        raise error_type('Encrypted credential has an invalid format')
    return version, payload


def _decrypt_with(fernet: Fernet, payload: str, error_type: type[Exception]) -> str:
    try:
        return fernet.decrypt(payload.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError, ValueError, TypeError):
        raise error_type('Encrypted credential could not be authenticated') from None


def decrypt_token(cipher_token: str) -> str:
    """Decrypt only current, authenticated ciphertext; never return input on failure."""
    if not cipher_token:
        return ''
    settings = _load_secret_settings()
    version, payload = _split_ciphertext(cipher_token)
    if version != settings.version:
        raise SecretDecryptionError('Encrypted credential uses a non-current key version')
    return _decrypt_with(settings.fernet, payload, SecretDecryptionError)


def _legacy_fernet(legacy_key_material: str) -> Fernet:
    """Reproduce the pre-versioning derivation only for explicit migration."""
    if not legacy_key_material or not legacy_key_material.strip():
        raise SecretMigrationError('An explicit legacy secret is required for migration')
    key_bytes = legacy_key_material.encode('utf-8')
    legacy_key = base64.urlsafe_b64encode(key_bytes.ljust(32, b'\0')[:32])
    try:
        return Fernet(legacy_key)
    except (ValueError, TypeError) as exc:
        raise SecretMigrationError('The supplied legacy secret cannot decrypt legacy values') from exc


def migrate_legacy_ciphertext(
    cipher_token: str,
    *,
    legacy_key: Optional[str] = None,
    allow_legacy: bool = False,
) -> str:
    """Return a versioned value after an explicitly authorized legacy rewrite."""
    if not cipher_token:
        return ''
    if not (allow_legacy or _is_truthy(os.getenv(LEGACY_MIGRATION_FLAG))):
        raise SecretMigrationError(
            f'legacy migration requires {LEGACY_MIGRATION_FLAG}=true or allow_legacy=True'
        )

    settings = _load_secret_settings()
    version, payload = (
        _split_ciphertext(cipher_token, SecretMigrationError)
        if cipher_token.startswith('v')
        else ('legacy', cipher_token)
    )
    if version != 'legacy':
        if version != settings.version:
            raise SecretMigrationError('Only unversioned legacy values may be migrated')
        _decrypt_with(settings.fernet, payload, SecretMigrationError)
        return cipher_token

    configured_legacy_key = legacy_key or os.getenv('MAGISTRATE_LEGACY_SECRET_KEY', '')
    plain_token = _decrypt_with(
        _legacy_fernet(configured_legacy_key), payload, SecretMigrationError
    )
    return _encrypt_with(settings.fernet, settings.version, plain_token)


def rotate_encrypted_token(cipher_token: str) -> str:
    """Rewrite one current/previous-version value without accepting plaintext."""
    if not cipher_token:
        return ''
    settings = _load_secret_settings()
    version, payload = _split_ciphertext(cipher_token, SecretRotationError)
    if version == settings.version:
        _decrypt_with(settings.fernet, payload, SecretRotationError)
        return cipher_token
    if not settings.rotation_enabled or not settings.previous_fernet or version != settings.previous_version:
        raise SecretRotationError('Credential is not eligible for the configured key rotation')
    plain_token = _decrypt_with(settings.previous_fernet, payload, SecretRotationError)
    return _encrypt_with(settings.fernet, settings.version, plain_token)


@dataclass(frozen=True)
class CredentialRewriteReport:
    scanned: int
    rewritten: int


def _rewrite_oauth_credentials(
    transform: Callable[[str], str],
    *,
    limit: int = MAX_ROTATION_ROWS,
    apply: bool = False,
) -> CredentialRewriteReport:
    if limit < 1 or limit > MAX_ROTATION_ROWS:
        raise SecretRotationError(f'limit must be between 1 and {MAX_ROTATION_ROWS}')
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            'SELECT id, access_token_enc, refresh_token_enc FROM oauth_credentials LIMIT ?',
            (limit + 1,),
        ).fetchall()
        if len(rows) > limit:
            raise SecretRotationError(
                f'credential rewrite exceeds the bounded limit of {limit} rows'
            )

        updates = []
        for credential_id, access_token_enc, refresh_token_enc in rows:
            new_access = transform(access_token_enc)
            new_refresh = transform(refresh_token_enc) if refresh_token_enc else refresh_token_enc
            if new_access != access_token_enc or new_refresh != refresh_token_enc:
                updates.append((new_access, new_refresh, credential_id))

        if apply:
            # sqlite rolls this transaction back automatically if the batch
            # write fails, so a partial rotation cannot be committed.
            with conn:
                conn.executemany(
                    'UPDATE oauth_credentials SET access_token_enc = ?, refresh_token_enc = ? WHERE id = ?',
                    updates,
                )
        else:
            conn.rollback()
        return CredentialRewriteReport(scanned=len(rows), rewritten=len(updates))
    finally:
        conn.close()


def migrate_legacy_oauth_credentials(
    *,
    legacy_key: Optional[str] = None,
    allow_legacy: bool = False,
    limit: int = MAX_ROTATION_ROWS,
    apply: bool = False,
) -> CredentialRewriteReport:
    return _rewrite_oauth_credentials(
        lambda value: migrate_legacy_ciphertext(
            value, legacy_key=legacy_key, allow_legacy=allow_legacy
        ),
        limit=limit,
        apply=apply,
    )


def rotate_oauth_credentials(
    *,
    limit: int = MAX_ROTATION_ROWS,
    apply: bool = False,
) -> CredentialRewriteReport:
    return _rewrite_oauth_credentials(rotate_encrypted_token, limit=limit, apply=apply)

def init_db():
    validate_secret_configuration()
    if not DB_PATH:
        raise SecretConfigurationError(
            'MAGISTRATE_DB_PATH is required outside explicit development/test mode'
        )
    db_parent = os.path.dirname(DB_PATH)
    if not db_parent:
        raise SecretConfigurationError('MAGISTRATE_DB_PATH must name an absolute persistent path')
    if not os.path.isabs(DB_PATH):
        raise SecretConfigurationError('MAGISTRATE_DB_PATH must be an absolute persistent path')
    os.makedirs(db_parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        avatar_url TEXT,
        bio TEXT,
        active_theme TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS connected_accounts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_user_id TEXT,
        provider_username TEXT,
        status TEXT NOT NULL,
        scopes TEXT,
        created_at INTEGER,
        updated_at INTEGER,
        FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS oauth_credentials (
        id TEXT PRIMARY KEY,
        connected_account_id TEXT NOT NULL,
        access_token_enc TEXT NOT NULL,
        refresh_token_enc TEXT,
        expires_at INTEGER,
        FOREIGN KEY(connected_account_id) REFERENCES connected_accounts(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS provider_capabilities (
        id TEXT PRIMARY KEY,
        connected_account_id TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        FOREIGN KEY(connected_account_id) REFERENCES connected_accounts(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS execution_credentials (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        credential_key TEXT NOT NULL,
        secret_enc TEXT NOT NULL,
        created_at INTEGER,
        updated_at INTEGER,
        UNIQUE(user_id, credential_key),
        FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS execution_preferences (
        user_id TEXT PRIMARY KEY,
        profile_id TEXT,
        switching_behavior TEXT NOT NULL DEFAULT 'migrate',
        unavailable_behavior TEXT NOT NULL DEFAULT 'error',
        updated_at INTEGER,
        FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS gateway_sessions (
        session_id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        user_id TEXT NOT NULL,
        scopes TEXT NOT NULL,
        issued_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        revoked_at INTEGER
    )
    ''')

    # Attention actions are a separate authority from notification state.  The
    # action key binds one live source revision and exact target; outcomes are
    # retained so retries cannot execute the decision twice.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attention_action_outcomes (
        action_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        actor_session_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        decision_key TEXT NOT NULL,
        action TEXT NOT NULL,
        provider TEXT NOT NULL,
        target_id TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        status TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    ''')
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(attention_action_outcomes)")}
    if "item_id" not in columns:
        cursor.execute("ALTER TABLE attention_action_outcomes ADD COLUMN item_id TEXT NOT NULL DEFAULT ''")

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attention_action_confirmations (
        confirmation_hash TEXT PRIMARY KEY,
        action_key TEXT NOT NULL,
        user_id TEXT NOT NULL,
        actor_session_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_id TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        used_at INTEGER
    )
    ''')

    cursor.execute("SELECT user_id FROM user_profiles WHERE user_id = 'default_user'")
    if not cursor.fetchone():
        now = int(time.time())
        cursor.execute('''
        INSERT INTO user_profiles (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
        VALUES ('default_user', 'Spectre Operator', 'spectre@magistrate.io', '/uploads/avatars/default_avatar.png', 'Firstmate Master Operator', 'dusk-mountain', ?, ?)
        ''', (now, now))

    conn.commit()
    conn.close()

def get_profile(user_id: str = 'default_user') -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at FROM user_profiles WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'user_id': row[0],
            'name': row[1] or '',
            'email': row[2] or '',
            'avatar_url': row[3] or '/uploads/avatars/default_avatar.png',
            'bio': row[4] or '',
            'active_theme': row[5] or 'dusk-mountain',
            'created_at': row[6],
            'updated_at': row[7]
        }
    return {
        'user_id': user_id,
        'name': '',
        'email': '',
        'avatar_url': '/uploads/avatars/default_avatar.png',
        'bio': '',
        'active_theme': 'dusk-mountain',
        'created_at': int(time.time()),
        'updated_at': int(time.time())
    }

def update_profile(user_id: str = 'default_user', name: Optional[str] = None, email: Optional[str] = None, avatar_url: Optional[str] = None, bio: Optional[str] = None, active_theme: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time())

    curr = get_profile(user_id)
    new_name = name if name is not None else curr['name']
    new_email = email if email is not None else curr['email']
    new_avatar = avatar_url if avatar_url is not None else curr['avatar_url']
    new_bio = bio if bio is not None else curr['bio']
    new_theme = active_theme if active_theme is not None else curr.get('active_theme', 'dusk-mountain')

    cursor.execute('''
    INSERT INTO user_profiles (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        name=excluded.name,
        email=excluded.email,
        avatar_url=excluded.avatar_url,
        bio=excluded.bio,
        active_theme=excluded.active_theme,
        updated_at=excluded.updated_at
    ''', (user_id, new_name, new_email, new_avatar, new_bio, new_theme, now, now))

    conn.commit()
    conn.close()

    return get_profile(user_id)

def get_connected_accounts(user_id: str = 'default_user') -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # A stored 'connected' row is not by itself evidence of a usable account.
    # Join the credential so callers can tell a real, unexpired OAuth grant from
    # a stale row and never present a connected state without one.
    cursor.execute('''
    SELECT a.id, a.provider, a.provider_user_id, a.provider_username, a.status, a.scopes, a.updated_at,
           c.access_token_enc, c.expires_at
    FROM connected_accounts a
    LEFT JOIN oauth_credentials c ON c.connected_account_id = a.id
    WHERE a.user_id = ?
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            'id': r[0],
            'provider': r[1],
            'provider_user_id': r[2],
            'provider_username': r[3],
            'status': r[4],
            'scopes': r[5].split(',') if r[5] else [],
            'updated_at': r[6],
            'has_credential': bool(r[7]),
            'credential_expires_at': r[8] if isinstance(r[8], int) else None,
        })
    return result

def upsert_connected_account(
    user_id: str,
    provider: str,
    provider_username: str,
    status: str = 'connected',
    scopes: Optional[List[str]] = None,
    access_token: str = '',
    provider_user_id: str = '',
) -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time())

    account_id = f'{user_id}_{provider}'
    scopes_str = ','.join(scopes or [])
    provider_identity = provider_user_id or provider_username

    cursor.execute('''
    INSERT INTO connected_accounts (id, user_id, provider, provider_user_id, provider_username, status, scopes, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        provider_username=excluded.provider_username,
        status=excluded.status,
        scopes=excluded.scopes,
        updated_at=excluded.updated_at
    ''', (account_id, user_id, provider, provider_identity, provider_username, status, scopes_str, now, now))

    if access_token:
        cred_id = f'cred_{account_id}'
        enc_access = encrypt_token(access_token)
        cursor.execute('''
        INSERT INTO oauth_credentials (id, connected_account_id, access_token_enc, refresh_token_enc, expires_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token_enc=excluded.access_token_enc
        ''', (cred_id, account_id, enc_access, '', now + 86400 * 30))

    conn.commit()
    conn.close()

    return {
        'id': account_id,
        'provider': provider,
        'provider_username': provider_username,
        'status': status,
        'scopes': scopes
    }

def disconnect_account(user_id: str, provider: str) -> bool:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    account_id = f'{user_id}_{provider}'
    now = int(time.time())

    cursor.execute("UPDATE connected_accounts SET status = 'disconnected', updated_at = ? WHERE id = ?", (now, account_id))
    # A disconnected account must not retain a credential: leaving the row would
    # let a later listing reconstruct a connected-looking state without consent.
    cursor.execute('DELETE FROM oauth_credentials WHERE connected_account_id = ?', (account_id,))
    conn.commit()
    conn.close()
    return True


def get_execution_credential_status(user_id: str = 'default_user') -> Dict[str, bool]:
    """Return only credential-presence flags; secret values never leave this module."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            'SELECT credential_key, secret_enc FROM execution_credentials WHERE user_id = ?',
            (user_id,),
        ).fetchall()
        return {key: bool(value) for key, value in rows}
    finally:
        conn.close()


def save_execution_credential(user_id: str, credential_key: str, secret: str) -> Dict[str, Any]:
    if not credential_key or not secret:
        raise ValueError('A provider and credential are required.')
    init_db()
    now = int(time.time())
    encrypted = encrypt_token(secret)
    credential_id = f'{user_id}_{credential_key}'
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('''
        INSERT INTO execution_credentials (id, user_id, credential_key, secret_enc, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, credential_key) DO UPDATE SET secret_enc=excluded.secret_enc, updated_at=excluded.updated_at
        ''', (credential_id, user_id, credential_key, encrypted, now, now))
        conn.commit()
    finally:
        conn.close()
    return {'credential_key': credential_key, 'configured': True, 'updated_at': now}


def delete_execution_credential(user_id: str, credential_key: str) -> bool:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('DELETE FROM execution_credentials WHERE user_id = ? AND credential_key = ?', (user_id, credential_key))
        conn.commit()
    finally:
        conn.close()
    return True


def get_execution_preferences(user_id: str = 'default_user') -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            'SELECT profile_id, switching_behavior, unavailable_behavior FROM execution_preferences WHERE user_id = ?',
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return {
        'profile_id': row[0] if row else None,
        'switching_behavior': row[1] if row else 'migrate',
        'unavailable_behavior': row[2] if row else 'error',
    }


def save_execution_preferences(
    user_id: str = 'default_user',
    *,
    profile_id: Optional[str] = None,
    switching_behavior: str = 'migrate',
    unavailable_behavior: str = 'error',
) -> Dict[str, Any]:
    if switching_behavior not in {'migrate', 'new-session'}:
        raise ValueError('Switching behavior must be migrate or new-session.')
    if unavailable_behavior not in {'error', 'fallback'}:
        raise ValueError('Unavailable behavior must be error or fallback.')
    init_db()
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('''
        INSERT INTO execution_preferences (user_id, profile_id, switching_behavior, unavailable_behavior, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET profile_id=excluded.profile_id,
          switching_behavior=excluded.switching_behavior, unavailable_behavior=excluded.unavailable_behavior,
          updated_at=excluded.updated_at
        ''', (user_id, profile_id, switching_behavior, unavailable_behavior, now))
        conn.commit()
    finally:
        conn.close()
    return {'profile_id': profile_id, 'switching_behavior': switching_behavior, 'unavailable_behavior': unavailable_behavior}

init_db()
