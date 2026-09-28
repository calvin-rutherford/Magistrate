"""Production-only configuration boundaries; never discover URLs from user/model input."""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

DEVELOPMENT_MODES = {'dev', 'development', 'test', 'testing'}


def production_mode() -> bool:
    return os.getenv('MAGISTRATE_ENV', '').strip().lower() not in DEVELOPMENT_MODES


def cors_origins() -> list[str]:
    raw = os.getenv('MAGISTRATE_CORS_ORIGINS')
    if raw is None and not production_mode():
        return ['http://localhost:8081', 'http://localhost:19006']
    origins = [item.strip() for item in (raw or '').split(',') if item.strip()]
    if not origins:
        raise RuntimeError('MAGISTRATE_CORS_ORIGINS requires explicit origins.')
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                     and parsed.port != 0 and parsed.username is None and parsed.password is None
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and '*' not in origin and not any(c.isspace() for c in origin)
                     and '\\' not in origin)
        except ValueError:
            valid = False
        if not valid or (production_mode() and parsed.scheme != 'https'):
            raise RuntimeError('MAGISTRATE_CORS_ORIGINS must contain exact HTTPS origins in production.')
    return origins


def validate_provider_url(value: str) -> None:
    """An operator allowlist, not a DNS firewall. Enforce egress policy at the host too."""
    try:
        url = urlsplit(value)
        valid = (url.scheme == 'https' and url.hostname and url.username is None
                 and url.password is None and not url.query and not url.fragment
                 and url.port in {None, 443} and '\\' not in value
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError('Provider URL must be a credential-free HTTPS URL on port 443.')
    if not production_mode():
        return
    allowed = {h.strip().lower() for h in os.getenv(
        'MAGISTRATE_PROVIDER_ALLOWED_HOSTS', 'api.openai.com,api.anthropic.com,generativelanguage.googleapis.com',
    ).split(',') if h.strip()}
    host = url.hostname.lower()
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if (host not in allowed or is_ip or '.' not in host or host.endswith(
            ('.localhost', '.local', '.internal', '.test', '.invalid', '.example'))):
        raise RuntimeError('Provider host is not permitted by the production egress allowlist.')


def validate_production_configuration() -> None:
    if not production_mode():
        return
    if os.getenv('MAGISTRATE_ENV', '').strip().lower() != 'production':
        raise RuntimeError('Set MAGISTRATE_ENV explicitly to production or a development/test mode.')
    cors_origins()
    for name, default, permitted in (
        ('MAGISTRATE_NATIVE_CHAT_ENABLED', 'true', {'true', '1', 'yes', 'on'}),
        ('MAGISTRATE_LEGACY_CHAT_ENABLED', 'false', {'false', '0', 'no', 'off'}),
        ('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'false', {'false', '0', 'no', 'off'}),
        ('MAGISTRATE_DEV_AUTO_SESSION', 'false', {'false', '0', 'no', 'off'}),
    ):
        if os.getenv(name, default).strip().lower() not in permitted:
            raise RuntimeError(f'{name} selects a retired or unsafe production mode.')
    secret = os.getenv('MAGISTRATE_BOOTSTRAP_SECRET', '')
    if len(secret) < 32 or secret.startswith(('replace-', 'your_', 'test-', 'dev-')):
        raise RuntimeError('Production bootstrap authority requires a random secret of at least 32 characters.')
    # Routing owns the catalog. Keep this validator free of DB/service imports;
    # adapters validate their effective endpoints before sending credentials.
    for name in ('OPENAI_BASE_URL', 'MAGISTRATE_MAGI_PROVIDER_URL'):
        if os.getenv(name):
            validate_provider_url(os.environ[name])
    metrics_token = os.getenv('MAGISTRATE_METRICS_TOKEN', '')
    if metrics_token and len(metrics_token) < 32:
        raise RuntimeError('MAGISTRATE_METRICS_TOKEN must have at least 32 random characters.')
