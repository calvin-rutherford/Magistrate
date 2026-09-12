"""Explicit native/legacy chat feature boundaries."""
from __future__ import annotations

import os

NATIVE_CHAT_DEFAULT_ENABLED = True
LEGACY_CHAT_DEFAULT_ENABLED = False


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} has an invalid boolean value.")


def native_chat_enabled() -> bool:
    return _flag("MAGISTRATE_NATIVE_CHAT_ENABLED", NATIVE_CHAT_DEFAULT_ENABLED)


def legacy_chat_enabled() -> bool:
    return _flag("MAGISTRATE_LEGACY_CHAT_ENABLED", LEGACY_CHAT_DEFAULT_ENABLED)


def validate_chat_feature_configuration() -> tuple[bool, bool]:
    """Resolve the mutually exclusive normal and rollback transports."""
    native = native_chat_enabled()
    legacy = legacy_chat_enabled()
    if native == legacy:
        raise RuntimeError("Exactly one of native chat and legacy chat must be enabled.")
    return native, legacy
