"""Pure schema/serialization tests: no worker, database or provider activation."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    DecisionBindingV2, DecisionRuntimeAcknowledgmentV2, FirstmateRuntimeAcknowledgmentV2,
    FirstmateRuntimeEnvelopeV2, ObjectiveIdentityV3, ObjectiveStateContract,
    ObjectiveStateV3, ReplayIdentityV3, RuntimeSpendV2, VoiceSessionV1,
)
from scripts.export_client_protocol import foundation_schemas, objective_protocol_typescript


IDENTITY = dict(owner_principal_id='owner', project_id='project_1', objective_id='objective_1',
                job_id='job_1', run_id='run_1', conversation_id='mgc_thread',
                user_message_id='mgm_origin', idempotency_key='dedupe_1', request_sha256='a' * 64)
EVENT = dict(source_instance_id='firstmate:test', source_event_id='event_1',
             event_sha256='b' * 64, replay_cursor=1)


def state(**changes):
    return dict(identity=IDENTITY, revision=1, state='accepted', dispatch='acknowledged',
                last_event=EVENT, **changes)


def test_client_output_is_generated_from_one_owner():
    target = Path(__file__).resolve().parents[2] / 'frontend/src/protocol/ObjectiveStateProtocol.ts'
    assert target.read_text() == objective_protocol_typescript()
    schemas = foundation_schemas()
    assert len(schemas) == 8
    assert schemas['objective_state_v3']['properties']['schema_version']['const'] == 'magistrate.objective-state.v3'
    assert schemas['objective_state_legacy']['properties']['schema_version']['const'] == 'magistrate.objective-state.v1'


@pytest.mark.parametrize('phase', ['accepted', 'running', 'verifying', 'waiting_for_input', 'failed', 'cancelled', 'completed'])
def test_lifecycle_is_observed_and_round_trips(phase):
    payload = state()
    payload['state'] = phase
    if phase == 'cancelled':
        payload['cancellation'] = 'observed'
    if phase == 'completed':
        payload['completion_evidence_sha256'] = 'c' * 64
    result = ObjectiveStateV3.model_validate(payload)
    assert ObjectiveStateV3.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        result.identity.job_id = 'replacement'
    with pytest.raises(ValidationError):
        result.revision = 2


@pytest.mark.parametrize('changes', [
    {'last_event': None}, {'dispatch': 'uncertain'}, {'state': 'completed'},
    {'state': 'cancelled'}, {'cancellation': 'observed'},
    {'completion_evidence_sha256': 'c' * 64}, {'revision': True},
    {'revision': 9007199254740992}, {'state': 'succeeded'}, {'state': 'working'},
    {'runtime_limits_enforced': True},
])
def test_no_fabricated_lifecycle_or_boolean_capability(changes):
    with pytest.raises(ValidationError):
        ObjectiveStateV3.model_validate(state() | changes)


def test_uncertain_dispatch_and_cancel_request_remain_nonterminal():
    payload = state() | {'state': None, 'last_event': None, 'dispatch': 'uncertain', 'cancellation': 'requested'}
    result = ObjectiveStateV3.model_validate(payload)
    assert result.state is None
    assert result.identity == ObjectiveIdentityV3.model_validate(IDENTITY)
    # Persisted v1 compatibility remains unchanged and accepts its original name.
    old = ObjectiveStateContract(objective_id='objective_1', owner_principal_id='owner',
                                 revision=1, state='succeeded', progress_stage='terminal')
    assert old.terminal


def envelope():
    return dict(identity=IDENTITY, repository=dict(repository_id=1, installation_id=2,
                full_name='owner/repository', base_commit='c' * 40), max_implementation_workers=2,
                max_execution_seconds=1800, objective_spend_limit_micro_usd=100,
                monthly_spend_limit_micro_usd=200, acknowledgment_deadline_ms=1234,
                cancellation_grace_seconds=10)


def test_requested_limits_are_bounded_not_capabilities():
    value = FirstmateRuntimeEnvelopeV2.model_validate(envelope())
    assert value.feature == 'PERSONAL_RUNTIME_V2'
    for changes in ({'max_implementation_workers': 3}, {'max_execution_seconds': 0},
                    {'objective_spend_limit_micro_usd': 201}, {'limits_enforced': True}):
        with pytest.raises(ValidationError):
            FirstmateRuntimeEnvelopeV2.model_validate(envelope() | changes)


def test_only_an_acknowledged_receipt_can_claim_enforcement():
    payload = dict(identity=IDENTITY, envelope_sha256='d' * 64, producer_revision='e' * 40,
                   event=EVENT, acknowledged_at_ms=1000, status='unsupported',
                   repository_enforcement='unavailable', concurrency_enforcement='unavailable',
                   duration_enforcement='unavailable', cancellation_enforcement='unavailable',
                   spend_enforcement='unavailable')
    FirstmateRuntimeAcknowledgmentV2.model_validate(payload)
    with pytest.raises(ValidationError):
        FirstmateRuntimeAcknowledgmentV2.model_validate(payload | {'duration_enforcement': 'enforced'})
    FirstmateRuntimeAcknowledgmentV2.model_validate(payload | {'status': 'acknowledged', 'duration_enforcement': 'enforced'})


def test_reservation_does_not_fabricate_measured_cost():
    payload = dict(measurement='unavailable', reserved_micro_usd=50, limit_micro_usd=100,
                   enforcement='admission-only')
    assert RuntimeSpendV2.model_validate(payload).measured_micro_usd is None
    for changes in ({'measured_micro_usd': 0}, {'measurement': 'final'},
                    {'measurement': 'partial', 'measured_micro_usd': 25},
                    {'enforcement': 'runtime-hard-limit'}):
        with pytest.raises(ValidationError):
            RuntimeSpendV2.model_validate(payload | changes)
    RuntimeSpendV2.model_validate(payload | {'measurement': 'final', 'measured_micro_usd': 25,
                                            'evidence_event_id': 'usage_event'})
    RuntimeSpendV2.model_validate(payload | {'enforcement': 'runtime-hard-limit',
                                            'runtime_acknowledgment_event_id': 'ack_event'})


def test_decision_receipt_is_exact_revision_request_and_canonical_answer_binding():
    binding = dict(identity=IDENTITY, decision_id='fmd_' + '1' * 32, decision_revision=2,
                   request_id='request_1', scope='project_1', expires_at_ms=2000)
    original = DecisionBindingV2.model_validate(binding)
    with pytest.raises(ValidationError):
        DecisionBindingV2.model_validate(binding | {'scope': 'another-project'})
    receipt = DecisionRuntimeAcknowledgmentV2(binding=original, answer_user_message_id='mgm_answer',
        answer_sha256='a' * 64, event=ReplayIdentityV3.model_validate(EVENT), status='received', acknowledged_at_ms=1234)
    assert receipt.status != 'applied'
    with pytest.raises(ValidationError):
        DecisionRuntimeAcknowledgmentV2.model_validate(json.loads(receipt.model_dump_json()) | {'answer_text': 'forged'})


def test_voice_continuity_requires_bound_turn_and_auth_receipt():
    payload = dict(session_id='voice_1', session_generation=1, owner_principal_id='owner',
                   conversation_id='mgc_thread', connection_id='conn_1', last_event_sequence=0,
                   authorization='unavailable', transport='unavailable')
    VoiceSessionV1.model_validate(payload)
    for changes in ({'transport': 'connected'}, {'authorization': 'authorized'}, {'turn_id': 'turn_1'}):
        with pytest.raises(ValidationError):
            VoiceSessionV1.model_validate(payload | changes)
    live = payload | dict(authorization='authorized', authorization_event_id='auth_event',
                         expires_at_ms=2000, transport='connected', turn_id='turn_1', client_message_id='voice_msg_1')
    assert VoiceSessionV1.model_validate(live).client_message_id == 'voice_msg_1'
    with pytest.raises(ValidationError):
        VoiceSessionV1.model_validate(live | {'authorization': 'revoked'})
