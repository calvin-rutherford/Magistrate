import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.herdr_client import HerdrClient


FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.mark.asyncio
async def test_missing_herdr_cli_is_a_degraded_state_not_a_gateway_error(monkeypatch):
    monkeypatch.setattr('asyncio.create_subprocess_exec', AsyncMock(side_effect=FileNotFoundError))
    client = HerdrClient(socket_path='/missing/herdr.sock')

    snapshot = await client.get_snapshot()
    assert snapshot['agents'] == []
    assert await client.read_agent_output('captain') == ''
    prompt = await client.prompt_agent('captain', 'hello')
    assert prompt['status'] == 'error'
    assert prompt['error'] == 'Herdr is unavailable.'


@pytest.mark.asyncio
async def test_resolve_target_uses_the_deployed_firstmate_workspace_not_an_active_pi_worker():
    """Regression: the deployed captain prompt was injected into the task worker."""
    snapshot = json.loads((FIXTURES / 'deployed-captain-routing.json').read_text())
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value=snapshot)

    assert await client.resolve_target('captain') == 'w4:p1'


@pytest.mark.asyncio
async def test_resolve_target_fails_closed_when_only_workers_exist():
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value={
        'workspaces': [{'workspace_id': 'worker-space', 'label': 'release worker'}],
        'agents': [{
            'workspace_id': 'worker-space', 'pane_id': 'pi-worker',
            'terminal_title_stripped': 'π - Magistrate', 'agent': 'pi',
        }],
    })

    assert await client.resolve_target('captain') == 'captain'


@pytest.mark.asyncio
async def test_list_agents_preserves_live_identity_without_demo_defaults():
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value={
        'agents': [
            {
                'pane_id': 'w1:p7',
                'agent_status': 'working',
                'agent': 'codex',
                'terminal_title_stripped': 'Magistrate worker',
                'tab_id': 'w1:t7',
                'workspace_id': 'w1',
            },
            {'pane_id': 'w1:p8', 'agent_status': None},
            {'agent_status': 'working'},
        ]
    })

    agents = await client.list_agents()

    assert agents == [
        {
            'id': 'w1:p7',
            'name': 'Magistrate worker',
            'harness': 'codex',
            'status': 'working',
            'pane_id': 'w1:p7',
            'tab_id': 'w1:t7',
            'workspace_id': 'w1',
        },
        {
            'id': 'w1:p8',
            'name': None,
            'harness': None,
            'status': 'unknown',
            'pane_id': 'w1:p8',
            'tab_id': None,
            'workspace_id': None,
        },
    ]


@pytest.mark.asyncio
async def test_list_agents_prefers_real_name_over_generic_harness_title_and_never_exposes_ids():
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value={
        'agents': [
            {'pane_id': 'w1:p1', 'name': 'Build worker', 'terminal_title_stripped': 'Magistrate', 'tab_id': 'w1:t1'},
            {'pane_id': 'w1:p2', 'name': 'firstmate', 'terminal_title_stripped': 'π - firstmate', 'tab_id': 'w1:t2'},
        ]
    })
    agents = await client.list_agents()
    assert agents[0]['name'] == 'Build worker'
    assert agents[1]['name'] is None
    assert agents[1]['name'] != agents[1]['pane_id']


@pytest.mark.asyncio
async def test_resolve_target_preserves_an_explicit_legacy_firstmate_name():
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value={
        'agents': [{'pane_id': 'w1:p1', 'name': 'firstmate', 'agent': 'pi'}],
    })

    assert await client.resolve_target('captain') == 'w1:p1'


@pytest.mark.asyncio
async def test_resolve_target_recognizes_firstmate_pi_workspace():
    client = HerdrClient()
    client.get_snapshot = AsyncMock(return_value={
        'workspaces': [
            {'workspace_id': 'w1', 'label': 'firstmate'},
            {'workspace_id': 'w2', 'label': 'release worker'},
        ],
        'agents': [
            {'workspace_id': 'w1', 'pane_id': 'w1:p1', 'agent': 'pi'},
            {'workspace_id': 'w2', 'pane_id': 'w2:p1', 'agent': 'pi'},
        ],
    })

    assert await client.resolve_target('captain') == 'w1:p1'
