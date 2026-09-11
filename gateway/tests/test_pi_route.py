import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.auth import Principal
from app.contracts import UniversalInputContract, VoiceMoveRequest
from app.conversation_store import list_messages, record_prompt
import app.main as main_module
from app.main import (
    _exchange_pi_dispatch, _ingest_target_snapshot, _pi_ownership_diagnostics,
    _pi_ownership_enabled, _reconcile_pi_ownership_once, create_voice_move,
    send_captain_prompt,
)
from app.pi_adapter_ipc import PiAdapterIPCError
from app.pi_ownership import get_pi_dispatch, has_pi_ownership


def principal():
    return Principal(
        f'pi-route-{uuid.uuid4().hex}', frozenset({'command', 'read'}),
        'pi-route-session', 4_102_444_800,
    )


@pytest.mark.asyncio
async def test_unset_flag_defaults_owned_prompt_to_only_local_pi_adapter(monkeypatch):
    owner = principal()
    exchange = AsyncMock(return_value={'state': 'bound', 'status': 'applied'})
    legacy_prompt = AsyncMock(side_effect=AssertionError('legacy provider must not be called'))
    monkeypatch.delenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', raising=False)
    monkeypatch.setattr('app.main._exchange_pi_dispatch', exchange)
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', legacy_prompt)

    response = await send_captain_prompt(UniversalInputContract(
        text='bind this exact prompt', message_id=f'msg-{uuid.uuid4().hex}',
    ), owner)

    exchange.assert_awaited_once()
    legacy_prompt.assert_not_awaited()
    dispatched = exchange.await_args.args[0]
    assert dispatched['prompt'] == 'bind this exact prompt'
    assert dispatched['capability'].startswith('pic_')
    assert has_pi_ownership(owner.user_id, 'captain', response['conversation']['turn_id'])
    encoded = json.dumps(response)
    assert dispatched['capability'] not in encoded
    assert dispatched['dispatch_incarnation'] not in encoded
    assert response['transport'] == 'pi-semantic'


@pytest.mark.parametrize('literal', ['1', 'true', 'yes', 'on', ' TRUE '])
def test_explicit_true_feature_flag_literals_enable(literal, monkeypatch):
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', literal)
    assert _pi_ownership_enabled() is True


@pytest.mark.parametrize('literal', ['0', 'false', 'no', 'off', ' FALSE '])
def test_explicit_false_feature_flag_literals_disable(literal, monkeypatch):
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', literal)
    assert _pi_ownership_enabled() is False


@pytest.mark.asyncio
async def test_unknown_feature_flag_fails_before_turn_or_legacy_dispatch(monkeypatch):
    owner = principal()
    legacy_prompt = AsyncMock(side_effect=AssertionError('invalid flag must fail closed'))
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'tru')
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', legacy_prompt)
    with pytest.raises(RuntimeError, match='invalid boolean'):
        await send_captain_prompt(UniversalInputContract(
            text='must not downgrade', message_id=f'msg-{uuid.uuid4().hex}',
        ), owner)
    legacy_prompt.assert_not_awaited()
    assert list_messages(owner.user_id, 'captain')['messages'] == []


@pytest.mark.asyncio
async def test_adapter_absence_keeps_prepared_ownership_and_never_falls_back(monkeypatch):
    owner = principal()
    legacy_prompt = AsyncMock(side_effect=AssertionError('legacy provider must not be called'))
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'true')
    monkeypatch.setattr(
        'app.main._exchange_pi_dispatch',
        AsyncMock(side_effect=PiAdapterIPCError('adapter-unavailable')),
    )
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', legacy_prompt)
    message_id = f'msg-{uuid.uuid4().hex}'

    with pytest.raises(HTTPException) as captured:
        await send_captain_prompt(UniversalInputContract(
            text='survive adapter restart', message_id=message_id,
        ), owner)
    assert getattr(captured.value, 'status_code', None) == 503
    legacy_prompt.assert_not_awaited()

    [message] = list_messages(owner.user_id, 'captain')['messages']
    assert message['client_message_id'] == message_id
    assert message['turn_status'] == 'awaiting_reply'
    state = get_pi_dispatch(
        owner.user_id, 'captain',
        # Ownership is intentionally not returned to the HTTP client, so join
        # through the canonical turn for this test only.
        next_dispatch_for_turn(message['turn_id']), include_secret=True,
    )
    assert state['state'] == 'prepared'

    recovered_exchange = AsyncMock(return_value={'state': 'bound', 'status': 'applied'})
    monkeypatch.setattr('app.main._exchange_pi_dispatch', recovered_exchange)
    retried = await send_captain_prompt(UniversalInputContract(
        text='survive adapter restart', message_id=message_id,
    ), owner)
    recovered_exchange.assert_awaited_once()
    replayed = recovered_exchange.await_args.args[0]
    assert replayed['dispatch_incarnation'] == state['dispatch_incarnation']
    assert replayed['capability'] == state['capability']
    assert retried['conversation']['turn_id'] == message['turn_id']
    assert len(list_messages(owner.user_id, 'captain')['messages']) == 1
    legacy_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabling_flag_cannot_resubmit_an_existing_owned_turn_to_legacy(monkeypatch):
    owner = principal()
    message_id = f'msg-{uuid.uuid4().hex}'
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'true')
    monkeypatch.setattr(
        'app.main._exchange_pi_dispatch',
        AsyncMock(return_value={'state': 'bound', 'status': 'applied'}),
    )
    await send_captain_prompt(UniversalInputContract(
        text='owned exactly once', message_id=message_id,
    ), owner)

    legacy_prompt = AsyncMock(side_effect=AssertionError('ownership survives feature disable'))
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'false')
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', legacy_prompt)
    with pytest.raises(HTTPException) as captured:
        await send_captain_prompt(UniversalInputContract(
            text='owned exactly once', message_id=message_id,
        ), owner)
    assert captured.value.status_code == 409
    legacy_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_channel_rejects_per_request_model_routing_without_creating_turn(monkeypatch):
    owner = principal()
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'true')
    exchange = AsyncMock()
    legacy_prompt = AsyncMock()
    monkeypatch.setattr('app.main._exchange_pi_dispatch', exchange)
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', legacy_prompt)
    with pytest.raises(HTTPException) as captured:
        await send_captain_prompt(UniversalInputContract(
            text='do not pretend to migrate', message_id=f'msg-{uuid.uuid4().hex}',
            harness='pi', model='different-model',
        ), owner)
    assert captured.value.status_code == 409
    assert list_messages(owner.user_id, 'captain')['messages'] == []
    exchange.assert_not_awaited()
    legacy_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_poll_does_not_read_display_transport(monkeypatch):
    owner = principal()
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'true')
    monkeypatch.setattr(
        'app.main._exchange_pi_dispatch',
        AsyncMock(return_value={'state': 'bound', 'status': 'applied'}),
    )
    typed = AsyncMock(side_effect=AssertionError('owned polling must not read legacy output'))
    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', typed)
    await send_captain_prompt(UniversalInputContract(
        text='owned poll', message_id=f'msg-{uuid.uuid4().hex}',
    ), owner)
    assert await _ingest_target_snapshot(owner.user_id, 'captain') is None
    typed.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_cannot_bypass_default_enabled_pi_ownership(monkeypatch):
    owner = principal()
    legacy_move = AsyncMock(side_effect=AssertionError('voice must fail before legacy dispatch'))
    monkeypatch.delenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', raising=False)
    monkeypatch.setattr('app.main.voice_move_service.handle', legacy_move)
    with pytest.raises(HTTPException) as captured:
        await create_voice_move(VoiceMoveRequest(
            utterance='send this to the captain', idempotency_key='pi-voice-test',
        ), owner)
    assert captured.value.status_code == 503
    legacy_move.assert_not_awaited()
    assert list_messages(owner.user_id, 'captain')['messages'] == []


@pytest.mark.asyncio
async def test_diagnostics_expose_truthful_bounded_ownership_state(monkeypatch):
    owner = principal()
    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'true')
    turn = record_prompt(
        owner.user_id, 'captain', f'msg-{uuid.uuid4().hex}', 'diagnose ownership',
        submitted_text='diagnose ownership', pi_semantic=True,
    )

    class ReadyClient:
        @classmethod
        def from_environment(cls):
            return cls()

        async def is_ready(self):
            return True

    monkeypatch.setattr(main_module, 'PiAdapterClient', ReadyClient)
    diagnostics = await _pi_ownership_diagnostics(owner.user_id, 'captain')
    assert diagnostics == {
        'schema_version': 'pi-semantic-ownership-diagnostics.v1',
        'enabled': True,
        'default_enabled': True,
        'defaulted': False,
        'selection': {
            'new_captain_turns': 'pi-semantic',
            'pi_semantic_selected': True,
        },
        'adapter': {'status': 'ready', 'ready': True},
        'dispatch_state_counts': {
            'prepared': 1, 'bound': 0, 'finalized': 0, 'failed': 0,
        },
        'recovery_backlog_count': 1,
        'terminal_fallback': {
            'policy': 'unowned-legacy-only', 'eligible_legacy_turns': False,
        },
    }
    serialized = json.dumps(diagnostics)
    assert turn['pi_dispatch']['capability'] not in serialized
    assert 'prompt_sha256' not in serialized
    assert 'dispatch_incarnation' not in serialized

    monkeypatch.delenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', raising=False)
    defaulted = await _pi_ownership_diagnostics(owner.user_id, 'captain')
    assert defaulted['enabled'] is True
    assert defaulted['defaulted'] is True

    monkeypatch.setenv('MAGISTRATE_PI_OWNERSHIP_ENABLED', 'false')
    disabled = await _pi_ownership_diagnostics(owner.user_id, 'captain')
    assert disabled['adapter'] == {'status': 'disabled', 'ready': False}
    assert disabled['selection']['pi_semantic_selected'] is False
    assert disabled['dispatch_state_counts']['prepared'] == 1
    assert disabled['terminal_fallback']['eligible_legacy_turns'] is False

    legacy_owner = principal()
    record_prompt(
        legacy_owner.user_id, 'captain', f'msg-{uuid.uuid4().hex}', 'legacy only',
    )
    legacy = await _pi_ownership_diagnostics(legacy_owner.user_id, 'captain')
    assert legacy['terminal_fallback']['eligible_legacy_turns'] is True


@pytest.mark.asyncio
async def test_semantic_recovery_does_not_consult_herdr(monkeypatch):
    dispatch = {'dispatch_incarnation': 'pdi_recovery_without_herdr'}
    exchange = AsyncMock(return_value={'state': 'bound'})
    monkeypatch.setattr(main_module, 'get_recoverable_dispatches', lambda: [dispatch])
    monkeypatch.setattr(main_module, '_exchange_pi_dispatch', exchange)
    monkeypatch.setattr(
        main_module.herdr_client, 'read_typed_rows',
        AsyncMock(side_effect=AssertionError('semantic recovery cannot read Herdr')),
    )
    monkeypatch.setattr(
        main_module.herdr_client, 'prompt_agent',
        AsyncMock(side_effect=AssertionError('semantic recovery cannot send through Herdr')),
    )

    await _reconcile_pi_ownership_once()
    exchange.assert_awaited_once_with(dispatch)


@pytest.mark.asyncio
async def test_adapter_receipt_is_sent_only_after_canonical_acceptance(monkeypatch):
    calls = []

    class Client:
        @classmethod
        def from_environment(cls):
            return cls()

        async def exchange(self, dispatch):
            calls.append('exchange')
            return {'signed': 'evidence'}

        async def acknowledge(self, dispatch, accepted_hash):
            calls.append(('ack', accepted_hash))

    dispatch = {
        'dispatch_incarnation': 'pdi_receipt_test_123456',
        'principal_id': 'receipt-owner', 'capability': 'pic_' + 'A' * 43,
        'state': 'bound',
    }
    monkeypatch.setattr(main_module, 'PiAdapterClient', Client)
    monkeypatch.setattr(main_module, 'mark_pi_dispatch_attempt', lambda value: calls.append('attempt'))
    monkeypatch.setattr(
        main_module, 'apply_pi_ownership_envelope',
        lambda *args: calls.append('canonical') or {
            'state': 'finalized', 'accepted_envelope_sha256': 'b' * 64,
        },
    )
    monkeypatch.setattr(
        main_module, 'mark_pi_adapter_acknowledged',
        lambda *args: calls.append('receipt-recorded'),
    )
    result = await _exchange_pi_dispatch(dispatch)
    assert result['state'] == 'finalized'
    assert calls == [
        'attempt', 'exchange', 'canonical', ('ack', 'b' * 64), 'receipt-recorded',
    ]


@pytest.mark.asyncio
async def test_recovery_accepts_matching_durable_adapter_receipt(monkeypatch):
    accepted_hash = 'c' * 64
    calls = []

    class Client:
        @classmethod
        def from_environment(cls):
            return cls()

        async def exchange(self, dispatch):
            return {
                'schema_version': 'magistrate.pi.ipc.v1',
                'event_type': 'acknowledged',
                'request_nonce': 'nonce_checked_by_client',
                'dispatch_incarnation': dispatch['dispatch_incarnation'],
                'accepted_envelope_sha256': accepted_hash,
            }

    dispatch = {
        'dispatch_incarnation': 'pdi_recovered_receipt_1234',
        'principal_id': 'receipt-owner', 'capability': 'pic_' + 'A' * 43,
        'state': 'finalized', 'accepted_envelope_sha256': accepted_hash,
    }
    monkeypatch.setattr(main_module, 'PiAdapterClient', Client)
    monkeypatch.setattr(main_module, 'mark_pi_dispatch_attempt', lambda value: None)
    monkeypatch.setattr(
        main_module, 'apply_pi_ownership_envelope',
        lambda *args: (_ for _ in ()).throw(AssertionError('receipt is not source evidence')),
    )
    monkeypatch.setattr(
        main_module, 'mark_pi_adapter_acknowledged',
        lambda *args: calls.append(args),
    )
    result = await _exchange_pi_dispatch(dispatch)
    assert result == {'state': 'finalized', 'status': 'retired'}
    assert calls and calls[0][-1] == accepted_hash


@pytest.mark.asyncio
async def test_lost_ack_response_converges_from_authenticated_unknown(monkeypatch):
    accepted_hash = 'd' * 64
    calls = []

    class Client:
        @classmethod
        def from_environment(cls):
            return cls()

        async def exchange(self, dispatch):
            raise PiAdapterIPCError('unknown-dispatch')

    dispatch = {
        'dispatch_incarnation': 'pdi_deleted_after_ack_1234',
        'principal_id': 'receipt-owner', 'capability': 'pic_' + 'A' * 43,
        'state': 'finalized', 'accepted_envelope_sha256': accepted_hash,
    }
    monkeypatch.setattr(main_module, 'PiAdapterClient', Client)
    monkeypatch.setattr(main_module, 'mark_pi_dispatch_attempt', lambda value: None)
    monkeypatch.setattr(
        main_module, 'mark_pi_adapter_acknowledged',
        lambda *args: calls.append(args),
    )
    result = await _exchange_pi_dispatch(dispatch)
    assert result == {'state': 'finalized', 'status': 'retired'}
    assert calls and calls[0][-1] == accepted_hash


def next_dispatch_for_turn(turn_id: str) -> str:
    # Keep production APIs tenant-qualified; tests can inspect the disposable
    # SQLite ledger to identify the opaque incarnation associated with a turn.
    import sqlite3
    from app import db
    with sqlite3.connect(db.DB_PATH) as conn:
        row = conn.execute(
            'SELECT dispatch_incarnation FROM pi_semantic_dispatches WHERE turn_id = ?',
            (turn_id,),
        ).fetchone()
    assert row is not None
    return row[0]
