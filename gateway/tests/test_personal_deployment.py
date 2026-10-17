"""Foundation policy tests, not a claim that main/intake are wired for personal mode."""
from dataclasses import replace
import sqlite3
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import db
from app.auth import Principal, authenticate_request, issue_session, revoke_session
from app.billing import BillingError, CreditLedger
from app.contracts import NativeMagiMessageContract, ObjectiveStateContract, PersonalDeploymentContract
from app.deployment_mode import (
    DeploymentMode, DeploymentModeError, PersonalResourceUsage,
    advance_objective_state, project_objective_state,
)


OWNER = 'personal-engineer'  # Deliberately has no "owner" prefix.
ENV = {
    'MAGISTRATE_DEPLOYMENT_MODE': 'personal',
    'MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID': OWNER,
    'MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS': '2',
    'MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD': '5000000',
    'MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD': '25000000',
}


def principal(user_id=OWNER, scopes=frozenset({'read', 'command', 'account'})):
    return Principal(user_id, scopes, 'fixture-session', int(time.time()) + 600)


def usage(**overrides):
    return PersonalResourceUsage.model_validate({
        'owner_principal_id': OWNER, 'observation_complete': True,
        'active_objectives': 0, 'active_implementation_workers': 0,
        'spent_micro_usd': 0, 'reserved_micro_usd': 0, **overrides,
    })


def state(**overrides):
    return project_objective_state(
        **{'objective_id': 'mgo_example', 'owner_principal_id': OWNER, 'revision': 1, **overrides},
    )


def test_explicit_personal_configuration_is_frozen_and_versioned():
    policy = DeploymentMode.from_env(ENV)
    assert policy.contract.schema_version == 'magistrate.personal-deployment.v1'
    assert policy.contract.owner_principal_id == OWNER
    assert policy.contract.max_implementation_workers == 2
    with pytest.raises(ValidationError):
        policy.contract.mode = 'public-saas'
    default_workers = {key: value for key, value in ENV.items() if not key.endswith('MAX_IMPLEMENTATION_WORKERS')}
    assert DeploymentMode.from_env(default_workers).contract.max_implementation_workers == 1


@pytest.mark.parametrize('name,value', [
    ('MAGISTRATE_DEPLOYMENT_MODE', ''), ('MAGISTRATE_DEPLOYMENT_MODE', 'Personal'),
    ('MAGISTRATE_DEPLOYMENT_MODE', 'owner'),
    ('MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID', ''),
    ('MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID', ' personal-engineer'),
    ('MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID', 'owner:*'),
    ('MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS', '3'),
    ('MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS', '0'),
    ('MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS', 'true'),
    ('MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD', '0'),
    ('MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD', '30000000'),
    ('MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD', 'unlimited'),
    ('MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD', '-1'),
    ('MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD', '1.0'),
    ('MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD', '9007199254740992'),
])
def test_bad_server_configuration_fails_closed_without_echoing_input(name, value):
    with pytest.raises(DeploymentModeError) as error:
        DeploymentMode.from_env({**ENV, name: value})
    assert error.value.status_code == 503
    assert str(error.value) in {'personal_configuration_invalid', 'deployment_mode_invalid'}


@pytest.mark.parametrize('missing', [
    'MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID',
    'MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD',
    'MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD',
])
def test_no_implicit_bootstrap_owner_or_spend_ceiling(missing):
    env = {key: value for key, value in ENV.items() if key != missing}
    env['MAGISTRATE_BOOTSTRAP_USER_ID'] = OWNER
    with pytest.raises(DeploymentModeError):
        DeploymentMode.from_env(env)


def test_public_saas_default_ignores_stale_personal_settings():
    for env in ({}, {**ENV, 'MAGISTRATE_DEPLOYMENT_MODE': 'public-saas'}):
        policy = DeploymentMode.from_env(env)
        for user_id in (OWNER, 'owner', 'owner-forged', 'other'):
            assert policy.requires_commercial_entitlement(principal(user_id))
            assert policy.checkout_allowed(principal(user_id))
            with pytest.raises(DeploymentModeError, match='personal_mode_required'):
                policy.check_personal_admission(principal(user_id), usage=usage())


@pytest.mark.parametrize('user_id', ['owner', 'owner-admin', 'owner.personal-engineer', 'Personal-engineer', 'other'])
def test_owner_like_principals_never_get_personal_authority(user_id):
    policy = DeploymentMode.from_env(ENV)
    for check in (policy.require_principal, policy.requires_commercial_entitlement, policy.checkout_allowed):
        with pytest.raises(DeploymentModeError, match='personal_owner_required'):
            check(principal(user_id))


def test_body_user_ids_and_dict_principals_cannot_grant_authority():
    policy = DeploymentMode.from_env(ENV)
    with pytest.raises(DeploymentModeError, match='authentication_required'):
        policy.requires_commercial_entitlement({'user_id': OWNER})
    with pytest.raises(ValidationError):
        NativeMagiMessageContract.model_validate({
            'client_message_id': 'forged-owner-001', 'content': 'implement',
            'user_id': OWNER, 'mode': 'personal',
        })
    with pytest.raises(ValidationError):
        PersonalDeploymentContract.model_validate({**policy.contract.model_dump(), 'bypass_approvals': True})


def test_personal_policy_preserves_scopes_and_expiry():
    policy = DeploymentMode.from_env(ENV)
    with pytest.raises(DeploymentModeError, match='scope_required'):
        policy.check_personal_admission(principal(scopes=frozenset({'account'})), usage=usage())
    with pytest.raises(DeploymentModeError, match='scope_required'):
        policy.checkout_allowed(principal(scopes=frozenset({'command'})))
    for expired in (replace(principal(), expires_at=0), replace(principal(), session_id='')):
        with pytest.raises(DeploymentModeError, match='authentication_required'):
            policy.requires_commercial_entitlement(expired)


def test_normal_auth_restoration_and_revocation_remain_authoritative(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'personal-auth.sqlite3'))
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', OWNER)
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'personal-test-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'read,account,command')
    token = issue_session('personal-test-secret')['session_token']
    policy = DeploymentMode.from_env(ENV)
    authorization = f'Bearer {token}'
    for _ in range(2):  # Restore through the real verifier, not a cached body id.
        resolved = authenticate_request(None, authorization)
        assert not policy.requires_commercial_entitlement(resolved)
    revoke_session(token)
    with pytest.raises(HTTPException) as revoked:
        policy.requires_commercial_entitlement(authenticate_request(None, authorization))
    assert revoked.value.status_code == 401


def test_billing_detachment_is_policy_only_and_never_fabricates_financial_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'personal-billing.sqlite3'))
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            'INSERT INTO user_profiles (user_id, name, email, created_at, updated_at) VALUES (?, ?, ?, 1, 1)',
            (OWNER, '', ''),
        )
    # This foundation has no financial writes; even historical data is untouched.
    def financial_rows():
        with sqlite3.connect(db.DB_PATH) as connection:
            return {table: connection.execute(f'SELECT * FROM {table}').fetchall() for table in (
                'billing_accounts', 'credit_ledger', 'credit_reservations', 'execution_usage_ledger',
            )}
    before = financial_rows()
    policy = DeploymentMode.from_env(ENV)
    assert not policy.requires_commercial_entitlement(principal())
    assert not policy.checkout_allowed(principal())
    assert policy.check_personal_admission(principal(), usage=usage()) == 5_000_000
    assert financial_rows() == before
    # Switching off keeps the existing ledger mandatory, not an unlimited free plan.
    assert DeploymentMode.from_env({}).requires_commercial_entitlement(principal())
    # Prove the real commercial ledger still refuses a second active reservation.
    ledger = CreditLedger()
    ledger.reserve_objective(OWNER, 'commercial-one', idempotency_key='commercial-one')
    with pytest.raises(BillingError) as denied:
        ledger.reserve_objective(OWNER, 'commercial-two', idempotency_key='commercial-two')
    assert denied.value.code == 'concurrency_limit'
    historical = financial_rows()
    assert not policy.requires_commercial_entitlement(principal())
    assert financial_rows() == historical


@pytest.mark.parametrize('workers', [0, 3, -1, True, 1.5, '1'])
def test_invalid_requested_worker_capacity_is_rejected(workers):
    with pytest.raises(DeploymentModeError, match='implementation_capacity_exceeded'):
        DeploymentMode.from_env(ENV).check_personal_admission(principal(), usage=usage(), implementation_workers=workers)


@pytest.mark.parametrize('observed', [
    {'active_implementation_workers': 2}, {'active_objectives': 2},
    {'active_implementation_workers': 100}, {'active_objectives': 100},
])
def test_unresolved_capacity_is_not_released_by_billing_detachment(observed):
    with pytest.raises(DeploymentModeError, match='implementation_capacity_exceeded'):
        DeploymentMode.from_env(ENV).check_personal_admission(principal(), usage=usage(**observed))


def test_two_worker_ceiling_and_full_objective_spend_reservation():
    policy = DeploymentMode.from_env(ENV)
    assert policy.check_personal_admission(principal(), usage=usage(), implementation_workers=2) == 5_000_000
    assert policy.check_personal_admission(principal(), usage=usage(
        active_objectives=1, active_implementation_workers=1,
        spent_micro_usd=15_000_000, reserved_micro_usd=5_000_000,
    )) == 5_000_000
    with pytest.raises(DeploymentModeError, match='personal_spend_limit_exceeded'):
        policy.check_personal_admission(principal(), usage=usage(
            spent_micro_usd=15_000_001, reserved_micro_usd=5_000_000,
        ))
    for held in ({'active_implementation_workers': 1}, {'active_objectives': 1}):
        with pytest.raises(DeploymentModeError, match='implementation_capacity_exceeded'):
            policy.check_personal_admission(principal(), usage=usage(**held), implementation_workers=2)


def test_missing_or_wrong_owner_observation_is_not_zero_usage():
    policy = DeploymentMode.from_env(ENV)
    with pytest.raises(DeploymentModeError, match='resource_observation_required'):
        policy.check_personal_admission(principal(), usage=None)
    with pytest.raises(DeploymentModeError, match='resource_owner_mismatch'):
        policy.check_personal_admission(principal(), usage=usage(owner_principal_id='other'))
    for bad in (
        {'spent_micro_usd': -1}, {'reserved_micro_usd': True}, {'active_objectives': '0'},
        {'observation_complete': False}, {'observation_complete': 1},
        {'observation_complete': 'true'},
    ):
        with pytest.raises(ValidationError):
            usage(**bad)


@pytest.mark.parametrize('facts,expected', [
    ({}, 'unknown'), ({'submission_status': 'future-status'}, 'unknown'),
    ({'submission_status': 'submitting'}, 'submitting'),
    ({'submission_status': 'accepted'}, 'queued'),
    ({'execution_phase': 'objective.accepted'}, 'accepted'),
    ({'execution_phase': 'worker.started'}, 'running'),
    ({'execution_phase': 'implementation.started'}, 'running'),
    ({'execution_phase': 'tests.passed'}, 'running'),
    ({'execution_phase': 'tests.failed'}, 'running'),
    ({'waiting_for_input': True}, 'waiting_for_input'),
    ({'submission_status': 'failed'}, 'unknown'),
    ({'submission_status': 'failed', 'recovering': True}, 'recovering'),
    ({'execution_phase': 'future.phase'}, 'unknown'),
    ({'observation_complete': False, 'execution_phase': 'worker.started'}, 'unknown'),
    ({'terminal_phase': 'future.terminal'}, 'unknown'),
    ({'terminal_phase': '', 'submission_status': 'accepted'}, 'unknown'),
    ({'terminal_phase': 'objective.completed'}, 'succeeded'),
    ({'terminal_phase': 'objective.failed'}, 'failed'),
    ({'terminal_phase': 'objective.cancelled'}, 'cancelled'),
])
def test_stable_objective_vocabulary_is_based_on_facts_not_process_presence(facts, expected):
    projected = state(**facts)
    assert projected.state == expected
    assert projected.schema_version == 'magistrate.objective-state.v1'
    assert projected.terminal == (expected in {'succeeded', 'failed', 'cancelled'})
    assert projected.revision == 1
    assert ObjectiveStateContract.model_validate_json(projected.model_dump_json()) == projected


def test_cancellation_request_is_not_observed_completion():
    pending = state(execution_phase='worker.started', cancellation_requested=True)
    assert pending.state == 'running' and not pending.terminal
    assert pending.cancellation == 'requested'
    recovering = state(revision=2, recovering=True, cancellation_requested=True)
    recovering = advance_objective_state(pending, recovering)
    assert recovering.state == 'recovering' and recovering.progress_stage == 'running'
    cancelled = state(revision=3, terminal_phase='objective.cancelled', cancellation_requested=True)
    assert advance_objective_state(recovering, cancelled).cancellation == 'observed'
    with pytest.raises(ValidationError):
        ObjectiveStateContract.model_validate({**pending.model_dump(), 'state': 'cancelled'})
    with pytest.raises(DeploymentModeError, match='objective_cancellation_conflict'):
        advance_objective_state(pending, state(revision=2))


@pytest.mark.parametrize('terminal', ['objective.completed', 'objective.failed', 'objective.cancelled'])
def test_terminal_truth_wins_over_recovery_and_late_progress(terminal):
    current = state(terminal_phase=terminal)
    incoming = state(revision=2, terminal_phase=terminal, recovering=True,
                     execution_phase='worker.started', observation_complete=False)
    assert advance_objective_state(current, incoming).state == current.state
    assert advance_objective_state(current, current) == current
    for regressed in (state(revision=2), state(revision=2, recovering=True),
                      state(revision=2, execution_phase='worker.started')):
        with pytest.raises(DeploymentModeError, match='objective_terminal_conflict'):
            advance_objective_state(current, regressed)


def test_revision_and_owner_identity_conflicts_fail_closed():
    current = state(revision=2, submission_status='accepted')
    for incoming, code in [
        (state(revision=1), 'objective_revision_stale'),
        (state(revision=2, execution_phase='worker.started'), 'objective_revision_conflict'),
        (state(revision=3, owner_principal_id='other'), 'objective_identity_conflict'),
        (state(revision=3, objective_id='other'), 'objective_identity_conflict'),
    ]:
        with pytest.raises(DeploymentModeError, match=code):
            advance_objective_state(current, incoming)
    accepted = state(revision=3, execution_phase='objective.accepted')
    running = state(revision=4, execution_phase='worker.started')
    assert advance_objective_state(current, accepted).state == 'accepted'
    assert advance_objective_state(accepted, running).state == 'running'


_STAGE_FACTS = {
    'submitting': {'submission_status': 'submitting'},
    'queued': {'submission_status': 'accepted'},
    'accepted': {'execution_phase': 'objective.accepted'},
    'running': {'execution_phase': 'worker.started'},
}
_REGRESSIONS = [
    ('queued', 'submitting'), ('accepted', 'submitting'), ('accepted', 'queued'),
    ('running', 'submitting'), ('running', 'queued'), ('running', 'accepted'),
]


@pytest.mark.parametrize('previous,regression', _REGRESSIONS)
def test_higher_revision_cannot_move_durable_acceptance_or_progress_backwards(previous, regression):
    with pytest.raises(DeploymentModeError, match='objective_transition_invalid'):
        advance_objective_state(state(**_STAGE_FACTS[previous]), state(revision=2, **_STAGE_FACTS[regression]))


@pytest.mark.parametrize('previous,regression', _REGRESSIONS)
@pytest.mark.parametrize('uncertainty', [{}, {'recovering': True}, {'waiting_for_input': True}])
def test_uncertainty_retains_progress_and_cannot_launder_a_retry(previous, regression, uncertainty):
    current = state(**_STAGE_FACTS[previous])
    raw_uncertain = state(revision=2, **uncertainty)
    uncertain = advance_objective_state(current, raw_uncertain)
    assert uncertain.state in {'unknown', 'recovering', 'waiting_for_input'}
    assert uncertain.progress_stage == previous
    # Replayed observations normalize to the same persisted high-water mark.
    assert advance_objective_state(uncertain, raw_uncertain) == uncertain
    with pytest.raises(DeploymentModeError, match='objective_transition_invalid'):
        advance_objective_state(uncertain, state(revision=3, **_STAGE_FACTS[regression]))
    resumed = advance_objective_state(uncertain, state(revision=3, **_STAGE_FACTS[previous]))
    assert resumed.state == previous
    completed = advance_objective_state(resumed, state(revision=4, terminal_phase='objective.completed'))
    assert completed.state == 'succeeded'
