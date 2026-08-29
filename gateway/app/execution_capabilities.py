import json
import os
import re
from typing import Any, Dict, List, Optional

from app.db import get_execution_credential_status


SAFE_CAPABILITY_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')


def _safe(value: Any, message: str) -> str:
    if not isinstance(value, str) or not SAFE_CAPABILITY_ID.fullmatch(value):
        raise ValueError(message)
    return value


def _validate_inventory(value: Any, credential_status: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get('harnesses'), list):
        raise ValueError('Execution inventory must contain a harnesses list.')

    harnesses: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []
    credential_status = credential_status or {}
    for raw_harness in value['harnesses']:
        if not isinstance(raw_harness, dict):
            raise ValueError('Each execution harness must be an object.')
        harness_id = _safe(raw_harness.get('id'), 'Execution harness IDs must be safe capability identifiers.')
        label = raw_harness.get('label')
        models = raw_harness.get('models')
        if not isinstance(label, str) or not label.strip() or not isinstance(models, list):
            raise ValueError(f'Execution harness {harness_id} has invalid metadata.')
        if raw_harness.get('verified') is not True:
            continue

        harness_provider = raw_harness.get('provider')
        if harness_provider is not None:
            harness_provider = _safe(harness_provider, 'Execution provider IDs must be safe capability identifiers.')
        checked_models = []
        for raw_model in models:
            if not isinstance(raw_model, dict):
                raise ValueError(f'Execution harness {harness_id} has an invalid model.')
            model_id = _safe(raw_model.get('id'), 'Execution model IDs must be safe capability identifiers.')
            model_label = raw_model.get('label')
            if not isinstance(model_label, str) or not model_label.strip():
                raise ValueError(f'Execution model {model_id} has invalid metadata.')
            provider = raw_model.get('provider', harness_provider or 'unknown')
            provider = _safe(provider, 'Execution provider IDs must be safe capability identifiers.')
            variant = raw_model.get('variant', model_id)
            variant = _safe(variant, 'Execution variant IDs must be safe capability identifiers.')
            profile_id = raw_model.get('profile_id', f'{harness_id}:{variant}')
            profile_id = _safe(profile_id, 'Execution profile IDs must be safe capability identifiers.')
            auth = raw_model.get('auth', raw_harness.get('auth', {}))
            if auth is None:
                auth = {}
            if not isinstance(auth, dict):
                raise ValueError(f'Execution model {model_id} has invalid auth metadata.')
            auth_required = auth.get('required', raw_model.get('auth_required', False)) is True
            credential_key = auth.get('credential_key', provider)
            credential_key = _safe(credential_key, 'Execution credential keys must be safe capability identifiers.')
            has_credential = bool(credential_status.get(credential_key))
            auth_state = 'configured' if has_credential else ('required' if auth_required else 'not-required')
            available = raw_model.get('available', True) is not False and auth_state != 'required'
            reason = raw_model.get('availability_reason')
            if not available and not isinstance(reason, str):
                reason = 'A compatible credential is required.' if auth_state == 'required' else 'This profile is unavailable.'
            profile = {
                'id': profile_id,
                'variant': variant,
                'label': raw_model.get('profile_label', model_label),
                'harness': {'id': harness_id, 'label': label},
                'provider': {'id': provider, 'label': raw_model.get('provider_label', provider)},
                'model': {'id': model_id, 'label': model_label},
                'verified': True,
                'available': available,
                'availability': 'available' if available else 'unavailable',
                'availability_reason': reason,
                'auth': {'required': auth_required, 'credential_key': credential_key, 'status': auth_state},
            }
            profiles.append(profile)
            # Keep the legacy harness/model projection wire-compatible for
            # older clients; the unified profiles list above is authoritative
            # for provider, variant, availability, and auth metadata.
            checked_models.append({'id': model_id, 'label': model_label})
        harnesses.append({'id': harness_id, 'label': label, 'verified': True, 'models': checked_models})
    return {'harnesses': harnesses, 'profiles': profiles}


def get_execution_capabilities(user_id: str = 'default_user') -> Dict[str, Any]:
    """Return verified execution profiles without exposing stored credentials.

    Herdr identifies a live harness but cannot advertise provider/model support,
    so the deployment inventory remains authoritative for selectable profiles.
    Credential presence is read as a boolean from Magistrate's encrypted store.
    """
    raw_inventory = os.getenv('MAGISTRATE_EXECUTION_INVENTORY')
    if not raw_inventory:
        return {
            'harnesses': [], 'profiles': [], 'source': 'gateway_configuration',
            'configured': False, 'routing': {'selection_supported': True, 'migration_supported': False, 'mode': 'prompt-context'},
        }
    try:
        checked = _validate_inventory(json.loads(raw_inventory), get_execution_credential_status(user_id))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Execution inventory is invalid: {exc}') from exc
    return {
        **checked, 'source': 'gateway_configuration', 'configured': True,
        'routing': {'selection_supported': True, 'migration_supported': False, 'mode': 'prompt-context'},
    }


def validate_execution_selection(harness_id: str, model_id: str, profile_id: Optional[str] = None, user_id: str = 'default_user') -> Dict[str, str]:
    try:
        capabilities = get_execution_capabilities(user_id)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    for profile in capabilities['profiles']:
        if profile['harness']['id'] != harness_id or profile['model']['id'] != model_id:
            continue
        if profile_id and profile['id'] != profile_id:
            continue
        if profile['availability'] != 'available':
            raise ValueError(profile['availability_reason'] or 'The selected execution profile is unavailable.')
        result = {'harness': harness_id, 'model': model_id}
        if profile_id:
            result.update({'profile_id': profile['id'], 'provider': profile['provider']['id'], 'variant': profile['variant']})
        return result
    raise ValueError('The selected harness and model are not available in the gateway inventory.')


def profile_selection(profile_id: str, user_id: str = 'default_user') -> Dict[str, str]:
    capabilities = get_execution_capabilities(user_id)
    for profile in capabilities['profiles']:
        if profile['id'] == profile_id:
            if profile['availability'] != 'available':
                raise ValueError(profile['availability_reason'] or 'The selected execution profile is unavailable.')
            return {'profile_id': profile['id'], 'harness': profile['harness']['id'], 'model': profile['model']['id'], 'provider': profile['provider']['id'], 'variant': profile['variant']}
    raise ValueError('The selected execution profile is not available.')
