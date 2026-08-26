import json
import os
import re
from typing import Any, Dict, List


SAFE_CAPABILITY_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')


def _validate_inventory(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get('harnesses'), list):
        raise ValueError('Execution inventory must contain a harnesses list.')

    harnesses = []
    for raw_harness in value['harnesses']:
        if not isinstance(raw_harness, dict):
            raise ValueError('Each execution harness must be an object.')
        harness_id = raw_harness.get('id')
        label = raw_harness.get('label')
        models = raw_harness.get('models')
        if not isinstance(harness_id, str) or not SAFE_CAPABILITY_ID.fullmatch(harness_id):
            raise ValueError('Execution harness IDs must be safe capability identifiers.')
        if not isinstance(label, str) or not label.strip() or not isinstance(models, list):
            raise ValueError(f'Execution harness {harness_id} has invalid metadata.')
        if raw_harness.get('verified') is not True:
            continue

        checked_models = []
        for raw_model in models:
            if not isinstance(raw_model, dict):
                raise ValueError(f'Execution harness {harness_id} has an invalid model.')
            model_id = raw_model.get('id')
            model_label = raw_model.get('label')
            if not isinstance(model_id, str) or not SAFE_CAPABILITY_ID.fullmatch(model_id):
                raise ValueError('Execution model IDs must be safe capability identifiers.')
            if not isinstance(model_label, str) or not model_label.strip():
                raise ValueError(f'Execution model {model_id} has invalid metadata.')
            checked_models.append({'id': model_id, 'label': model_label})

        harnesses.append({
            'id': harness_id,
            'label': label,
            'verified': True,
            'models': checked_models,
        })
    return harnesses


def get_execution_capabilities() -> Dict[str, Any]:
    """Return the only server-authoritative inventory exposed to execution clients.

    Availability is intentionally empty until the deployment supplies a verified
    inventory. The Herdr snapshot does not contain model capabilities, so this
    must not infer models from live pane metadata.
    """
    raw_inventory = os.getenv('MAGISTRATE_EXECUTION_INVENTORY')
    if not raw_inventory:
        return {'harnesses': [], 'source': 'gateway_configuration', 'configured': False}
    try:
        inventory = _validate_inventory(json.loads(raw_inventory))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Execution inventory is invalid: {exc}') from exc
    return {'harnesses': inventory, 'source': 'gateway_configuration', 'configured': True}


def validate_execution_selection(harness_id: str, model_id: str) -> Dict[str, str]:
    try:
        inventory = get_execution_capabilities()['harnesses']
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    for harness in inventory:
        if harness['id'] != harness_id:
            continue
        if any(model['id'] == model_id for model in harness['models']):
            return {'harness': harness_id, 'model': model_id}
        break
    raise ValueError('The selected harness and model are not available in the gateway inventory.')
