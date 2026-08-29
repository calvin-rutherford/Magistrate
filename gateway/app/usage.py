"""Read-only quota-axi integration for the drawer Usage view."""

import asyncio
import json
from typing import Any, Dict, List, Optional


async def get_usage(provider: Optional[str] = None) -> Dict[str, Any]:
    """Return quota-axi evidence without inventing values or shelling through a command line."""
    command = ['quota-axi', '--json']
    if provider:
        command.extend(['--provider', provider])
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    except (FileNotFoundError, asyncio.TimeoutError) as exc:
        raise RuntimeError('Usage data is unavailable.') from exc
    if process.returncode != 0:
        detail = stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(detail or 'Usage data is unavailable.')
    try:
        payload = json.loads(stdout.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('Usage provider returned invalid data.') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('providers'), list):
        raise RuntimeError('Usage provider returned invalid data.')
    return {
        'generated_at': payload.get('generatedAt'),
        'schema_version': payload.get('schemaVersion'),
        'providers': [_summarize_provider(item) for item in payload['providers'] if isinstance(item, dict)],
        'source': 'quota-axi',
    }


def _summarize_provider(raw: Dict[str, Any]) -> Dict[str, Any]:
    state = raw.get('state') if isinstance(raw.get('state'), dict) else {}
    windows: List[Dict[str, Any]] = []
    for window in raw.get('windows', []) if isinstance(raw.get('windows'), list) else []:
        if not isinstance(window, dict):
            continue
        item: Dict[str, Any] = {}
        for key in ('id', 'label', 'kind', 'resetsAt', 'percentRemaining', 'spentUsd', 'limitUsd'):
            if key in window and isinstance(window[key], (str, int, float)):
                item[key] = window[key]
        if item:
            windows.append(item)
    result: Dict[str, Any] = {
        'provider': raw.get('provider') if isinstance(raw.get('provider'), str) else 'unknown',
        'plan': raw.get('plan') if isinstance(raw.get('plan'), str) else None,
        'status': state.get('status') if isinstance(state.get('status'), str) else 'unknown',
        'stale': state.get('stale') if isinstance(state.get('stale'), bool) else None,
        'windows': windows,
    }
    if isinstance(state.get('error'), str):
        result['error'] = state['error']
    return result
