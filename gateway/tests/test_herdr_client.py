from unittest.mock import AsyncMock

import pytest

from app.herdr_client import HerdrClient


@pytest.mark.asyncio
async def test_resolve_target_prefers_pi_captain_over_unrelated_codex():
    client = HerdrClient()
    client.list_agents = AsyncMock(return_value=[
        {'id': 'codex-pane', 'name': 'reviewer', 'harness': 'codex'},
        {'id': 'pi-pane', 'name': 'π - firstmate', 'harness': 'pi'},
    ])

    assert await client.resolve_target('captain') == 'pi-pane'


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
            'name': 'w1:p8',
            'harness': None,
            'status': 'unknown',
            'pane_id': 'w1:p8',
            'tab_id': None,
            'workspace_id': None,
        },
    ]


@pytest.mark.asyncio
async def test_resolve_target_recognizes_firstmate_pi_pane():
    client = HerdrClient()
    client.list_agents = AsyncMock(return_value=[
        {'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'π - firstmate', 'harness': 'pi'},
        {'id': 'w1:p2', 'pane_id': 'w1:p2', 'name': 'π - Magistrate', 'harness': 'pi'},
    ])

    assert await client.resolve_target('captain') == 'w1:p1'
