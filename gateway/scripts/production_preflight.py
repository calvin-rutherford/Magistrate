"""Secret-free production preflight; never source a service env file as shell code."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import stat

from app.production_security import validate_production_configuration, validate_provider_url
from scripts.storage_ops import private_file
from urllib.parse import urlsplit


def load_service_environment(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Environment file must be a regular non-symlink file.')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Environment file must be service-owned mode 0600.')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, raw = line.partition('=')
        if not sep or not key.isidentifier() or not key.isascii() or key in values:
            raise ValueError('Invalid or duplicate environment assignment.')
        # Deliberately narrower than systemd: one literal value per line, with
        # optional whole-value quotes. No interpolation or continuation syntax.
        if raw[:1] in {'"', "'"}:
            parts = shlex.split(raw, comments=False)
            if len(parts) != 1:
                raise ValueError('Invalid quoted environment value.')
            value = parts[0]
        else:
            if any(c.isspace() for c in raw):
                raise ValueError('Environment values containing spaces must be quoted.')
            value = raw
        if any(c in value for c in ('\n', '\r', '\x00')):
            raise ValueError('Multiline environment values are not supported.')
        values[key] = value
    return values


def preflight(env_file: Path) -> None:
    values = load_service_environment(env_file)
    if values.get('MAGISTRATE_ENV') != 'production':
        raise ValueError('The service environment must explicitly select production.')
    # Do not let interactive developer settings fill gaps in the service file.
    os.environ.clear()
    os.environ.update(values)
    validate_production_configuration()
    from app.db import validate_secret_configuration
    from app.magi_routing import load_routing_catalog, RoutedMagiModel
    from app.billing import validate_billing_configuration
    from app.hosted_execution import HostedExecutionConfig
    validate_secret_configuration()
    legacy = values.get('MAGISTRATE_MAGI_MODEL_PROVIDER', '').strip().lower()
    if legacy and legacy != 'routed':
        raise ValueError('Use the routed model catalog, not retired provider selectors.')
    catalog = load_routing_catalog()
    configured = RoutedMagiModel(catalog).configured_provider_ids
    if not configured:
        raise ValueError('Native Magi requires server-side provider credentials.')
    defaults = {'openai': 'https://api.openai.com/v1',
                'anthropic': 'https://api.anthropic.com/v1',
                'google': 'https://generativelanguage.googleapis.com/v1beta'}
    for candidate in catalog.candidates:
        if candidate.enabled and candidate.available and candidate.provider in configured:
            credential = values.get(candidate.credential_env, '')
            if credential.startswith(('your_', 'replace-')):
                raise ValueError('Provider credentials must not be placeholders.')
            validate_provider_url(candidate.base_url or defaults[candidate.provider])
    checkout = Path(__file__).resolve().parents[2]
    db_path = values.get('MAGISTRATE_DB_PATH', '')
    database_url = values.get('MAGISTRATE_DATABASE_URL', '')
    if bool(db_path) == bool(database_url):
        raise ValueError('Configure exactly one database backend.')
    if database_url:
        url = urlsplit(database_url)
        if url.scheme not in {'postgres', 'postgresql'} or not url.hostname or any(c.isspace() for c in database_url):
            raise ValueError('Database URL must select PostgreSQL.')
        state = Path(values.get('MAGISTRATE_STATE_DIR', ''))
        _external_path(state, checkout)
        if (not state.is_dir() or state.stat().st_uid != os.getuid()
                or stat.S_IMODE(state.stat().st_mode) != 0o700):
            raise ValueError('PostgreSQL state directory must be service-owned mode 0700.')
    else:
        path = Path(db_path)
        _external_path(path, checkout)
        private_file(path)
        state = path.parent
    avatar = Path(values.get('MAGISTRATE_AVATAR_DIR') or state / 'avatars')
    objects = Path(values.get('MAGISTRATE_OBJECT_STORAGE_DIR') or values.get('MAGISTRATE_CHAT_UPLOAD_DIR') or state / 'private_objects')
    for path in (avatar, objects):
        _external_path(path, checkout)
    if avatar.is_relative_to(objects) or objects.is_relative_to(avatar):
        raise ValueError('Private objects and public avatars must not overlap.')
    if values.get('STRIPE_SECRET_KEY') or values.get('STRIPE_WEBHOOK_SECRET'):
        path = Path(values.get('MAGISTRATE_BILLING_CATALOG_PATH', ''))
        _external_path(path, checkout)
        private_file(path)
    validate_billing_configuration()
    HostedExecutionConfig.from_env()


def _external_path(path: Path, checkout: Path) -> None:
    if not path.is_absolute() or path.resolve() != path or path.is_relative_to(checkout):
        raise ValueError('Persistent paths must be normalized, external and symlink-free.')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('env_file', type=Path)
    args = parser.parse_args()
    try:
        preflight(args.env_file)
    except (ValueError, RuntimeError, OSError):
        # A parser error may contain a source line. Never print exception text.
        raise SystemExit('Production preflight failed; check private configuration and file permissions.') from None
    print('Production configuration validated.')


if __name__ == '__main__':
    main()
