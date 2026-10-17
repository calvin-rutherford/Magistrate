"""Owner-bound personal policy; no authentication, database, or runtime side effects.

Composition must load this once at startup and pass only Principals returned by
normal auth (including revocation checks). Personal admission checks are pure:
call under the durable reservation transaction, then reserve the returned full
objective ceiling *before* publication. They are not a scheduler or a ledger.
Nothing here enables personal mode merely by being imported.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.auth import Principal
from app.contracts import (
    MAX_CONTRACT_INTEGER,
    PRINCIPAL_ID_PATTERN,
    ObjectiveStateContract,
    PersonalDeploymentContract,
)


class DeploymentModeError(RuntimeError):
    """Closed, content-free policy failure for the integration boundary."""

    def __init__(self, code: str, status_code: int = 403) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PersonalResourceUsage(BaseModel):
    """Complete owner-scoped snapshot from a locked durable resource ledger.

    Count unresolved/unknown/recovering objectives and retained worker leases;
    absence of an observation must never be converted into zero usage. Monthly
    totals include consumed spend plus outstanding full-ceiling reservations,
    including reservations carried from an earlier month. This is not a client
    request contract and is not exported as an entitlement.
    """

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    owner_principal_id: str = Field(min_length=1, max_length=128, pattern=PRINCIPAL_ID_PATTERN)
    observation_complete: Literal[True]
    active_objectives: int = Field(ge=0, le=MAX_CONTRACT_INTEGER)
    active_implementation_workers: int = Field(ge=0, le=MAX_CONTRACT_INTEGER)
    spent_micro_usd: int = Field(ge=0, le=MAX_CONTRACT_INTEGER)
    reserved_micro_usd: int = Field(ge=0, le=MAX_CONTRACT_INTEGER)

    @field_validator('observation_complete', mode='before')
    @classmethod
    def require_complete_observation(cls, value: object) -> bool:
        if value is not True:
            raise ValueError('A complete persisted resource observation is required.')
        return True


@dataclass(frozen=True)
class DeploymentMode:
    contract: PersonalDeploymentContract

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> 'DeploymentMode':
        """Read only explicit server settings; invalid enabled settings fail closed.

        Stale personal settings have no effect while mode is public-saas. No
        bootstrap identity, user-id prefix, client flag, or subscription status
        can select personal mode or fill its required owner.
        """
        env = os.environ if environment is None else environment
        mode = env.get('MAGISTRATE_DEPLOYMENT_MODE', 'public-saas')
        if mode == 'public-saas':
            return cls(PersonalDeploymentContract())
        if mode != 'personal':
            raise DeploymentModeError('deployment_mode_invalid', 503)
        try:
            def integer(name: str, default: str | None = None) -> int:
                raw = env.get(name, default)
                if not isinstance(raw, str) or not re.fullmatch(r'[0-9]{1,16}', raw):
                    raise ValueError('Invalid bounded integer')
                return int(raw)

            return cls(PersonalDeploymentContract(
                mode='personal',
                owner_principal_id=env.get('MAGISTRATE_PERSONAL_OWNER_PRINCIPAL_ID'),
                max_implementation_workers=integer('MAGISTRATE_PERSONAL_MAX_IMPLEMENTATION_WORKERS', '1'),
                objective_spend_limit_micro_usd=integer('MAGISTRATE_PERSONAL_OBJECTIVE_SPEND_LIMIT_MICRO_USD'),
                monthly_spend_limit_micro_usd=integer('MAGISTRATE_PERSONAL_MONTHLY_SPEND_LIMIT_MICRO_USD'),
            ))
        except (ValueError, TypeError, ValidationError):
            # Do not echo operator configuration or Pydantic input values.
            raise DeploymentModeError('personal_configuration_invalid', 503) from None

    @property
    def personal(self) -> bool:
        return self.contract.mode == 'personal'

    def require_principal(self, principal: Principal, *, scope: str | None = None) -> Principal:
        """Additional gate AFTER normal authentication, not a token verifier.

        An already-resolved Principal cannot prove it has not since been revoked;
        HTTP/WS and recovery composition must retain their normal live auth checks.
        """
        if (
            not isinstance(principal, Principal)
            or not principal.session_id
            or principal.expires_at <= int(time.time())
        ):
            raise DeploymentModeError('authentication_required', 401)
        if self.personal and principal.user_id != self.contract.owner_principal_id:
            raise DeploymentModeError('personal_owner_required')
        if scope is not None and not principal.has(scope):
            raise DeploymentModeError('scope_required')
        return principal

    def requires_commercial_entitlement(self, principal: Principal) -> bool:
        self.require_principal(principal)
        return not self.personal

    def checkout_allowed(self, principal: Principal) -> bool:
        """Personal UI/routes must not initiate subscription or top-up checkout."""
        self.require_principal(principal, scope='account')
        return not self.personal

    def check_personal_admission(
        self,
        principal: Principal,
        *,
        usage: PersonalResourceUsage | None,
        implementation_workers: int = 1,
    ) -> int:
        """Return the micro-USD hold required for one NEW objective, or refuse.

        Caller owns atomic claim/reserve/idempotent replay, supervisor capacity,
        per-objective runtime hard stops, measured settlement, and recovery.
        Replays must load the existing reservation rather than calling this as
        new work. Never substitute this check for provider routing/spend gates.
        """
        self.require_principal(principal, scope='command')
        if not self.personal:
            raise DeploymentModeError('personal_mode_required')
        if not isinstance(usage, PersonalResourceUsage) or not usage.observation_complete:
            raise DeploymentModeError('resource_observation_required', 503)
        if usage.owner_principal_id != principal.user_id:
            raise DeploymentModeError('resource_owner_mismatch')
        if (
            type(implementation_workers) is not int
            or not 1 <= implementation_workers <= self.contract.max_implementation_workers
        ):
            raise DeploymentModeError('implementation_capacity_exceeded', 409)
        # Every unresolved objective retains at least one slot even before its
        # worker-start event arrives. Multi-worker reservations must also be
        # included in active_implementation_workers by the durable adapter.
        committed_workers = max(usage.active_objectives, usage.active_implementation_workers)
        if (
            usage.active_objectives >= self.contract.max_implementation_workers
            or committed_workers + implementation_workers > self.contract.max_implementation_workers
        ):
            raise DeploymentModeError('implementation_capacity_exceeded', 409)
        ceiling = self.contract.objective_spend_limit_micro_usd
        monthly = self.contract.monthly_spend_limit_micro_usd
        # Validated personal configuration guarantees both are positive integers.
        assert ceiling is not None and monthly is not None
        if usage.spent_micro_usd + usage.reserved_micro_usd + ceiling > monthly:
            raise DeploymentModeError('personal_spend_limit_exceeded', 409)
        return ceiling


_TERMINAL_STATES = {
    'objective.completed': 'succeeded',
    'objective.failed': 'failed',
    'objective.cancelled': 'cancelled',
}
_RUNNING_PHASES = frozenset({
    'worker.started', 'implementation.started', 'tests.started', 'tests.passed',
    'tests.failed', 'review.started',
})
_UNCERTAIN_STATES = frozenset({'unknown', 'recovering', 'waiting_for_input'})
_TERMINAL_PUBLIC_STATES = frozenset(_TERMINAL_STATES.values())
_PROGRESS_ORDER = {
    'unobserved': 0, 'submitting': 1, 'queued': 2, 'accepted': 3,
    'running': 4, 'terminal': 5,
}
# Forward observed progress may skip intermediate pushes; uncertainty and input
# waits may interrupt it but must retain the durable progress high-water mark.
_ALLOWED_TRANSITIONS = {
    'submitting': frozenset({'submitting', 'queued', 'accepted', 'running'}) | _UNCERTAIN_STATES | _TERMINAL_PUBLIC_STATES,
    'queued': frozenset({'queued', 'accepted', 'running'}) | _UNCERTAIN_STATES | _TERMINAL_PUBLIC_STATES,
    'accepted': frozenset({'accepted', 'running'}) | _UNCERTAIN_STATES | _TERMINAL_PUBLIC_STATES,
    'running': frozenset({'running'}) | _UNCERTAIN_STATES | _TERMINAL_PUBLIC_STATES,
    **{state: frozenset(_PROGRESS_ORDER) - {'unobserved', 'terminal'} | _UNCERTAIN_STATES | _TERMINAL_PUBLIC_STATES
       for state in _UNCERTAIN_STATES},
    **{state: frozenset({state}) for state in _TERMINAL_PUBLIC_STATES},
}


def project_objective_state(
    *,
    objective_id: str,
    owner_principal_id: str,
    revision: int,
    submission_status: str | None = None,
    execution_phase: str | None = None,
    terminal_phase: str | None = None,
    waiting_for_input: bool = False,
    recovering: bool = False,
    observation_complete: bool = True,
    cancellation_requested: bool = False,
) -> ObjectiveStateContract:
    """Project owner-filtered persisted facts, with no runtime observation/repair.

    Terminal phases must come from the validated execution store, not provider
    prose. Terminal authority wins over recovery, later nonterminal events and
    cancellation requests. A failed *submission* is recoverable, not evidence of
    terminal execution failure. Missing/new phases fail to unknown, never queued
    or completed by invention. The caller supplies the persisted revision.
    """
    terminal = terminal_phase if terminal_phase is not None else (
        execution_phase if execution_phase in _TERMINAL_STATES else None
    )
    if terminal in _TERMINAL_STATES:
        state = _TERMINAL_STATES[terminal]
    elif terminal is not None or not observation_complete:
        state = 'unknown'
    elif recovering:
        state = 'recovering'
    elif execution_phase is not None and execution_phase not in _RUNNING_PHASES | {'objective.accepted'}:
        state = 'unknown'
    elif waiting_for_input:
        state = 'waiting_for_input'
    elif execution_phase in _RUNNING_PHASES:
        state = 'running'
    elif execution_phase == 'objective.accepted':
        state = 'accepted'
    elif submission_status == 'accepted':
        state = 'queued'
    elif submission_status == 'submitting':
        state = 'submitting'
    else:
        state = 'unknown'
    if state in _TERMINAL_PUBLIC_STATES:
        progress_stage = 'terminal'
    elif execution_phase in _RUNNING_PHASES:
        progress_stage = 'running'
    elif execution_phase == 'objective.accepted':
        progress_stage = 'accepted'
    elif submission_status == 'accepted':
        progress_stage = 'queued'
    elif submission_status == 'submitting':
        progress_stage = 'submitting'
    else:
        progress_stage = 'unobserved'
    return ObjectiveStateContract(
        objective_id=objective_id,
        owner_principal_id=owner_principal_id,
        revision=revision,
        state=state,
        progress_stage=progress_stage,
        cancellation='observed' if state == 'cancelled' else 'requested' if cancellation_requested else 'none',
    )


def advance_objective_state(
    current: ObjectiveStateContract, incoming: ObjectiveStateContract,
) -> ObjectiveStateContract:
    """Validate a durable projection replacement under the caller's write lock.

    This is not persistence. Use a compare-and-set on the stored revision in the
    same transaction. No inference from text, timers, or a vanished process can
    revise terminal truth or silently release a cancellation/resource hold.
    """
    if (current.owner_principal_id, current.objective_id) != (
        incoming.owner_principal_id, incoming.objective_id,
    ):
        raise DeploymentModeError('objective_identity_conflict', 409)
    if incoming.revision < current.revision:
        raise DeploymentModeError('objective_revision_stale', 409)
    # Normalize an uncertain update before replay comparison: it cannot erase
    # prior durable progress merely because this observation lacks those facts.
    if (
        not current.terminal and incoming.state in _UNCERTAIN_STATES
        and _PROGRESS_ORDER[incoming.progress_stage] < _PROGRESS_ORDER[current.progress_stage]
    ):
        incoming = ObjectiveStateContract.model_validate({
            **incoming.model_dump(), 'progress_stage': current.progress_stage,
        })
    if incoming.revision == current.revision:
        if incoming != current:
            raise DeploymentModeError('objective_revision_conflict', 409)
        return current
    if current.terminal and incoming.state != current.state:
        raise DeploymentModeError('objective_terminal_conflict', 409)
    if (
        incoming.state not in _ALLOWED_TRANSITIONS[current.state]
        or _PROGRESS_ORDER[incoming.progress_stage] < _PROGRESS_ORDER[current.progress_stage]
    ):
        raise DeploymentModeError('objective_transition_invalid', 409)
    if current.cancellation != 'none' and incoming.cancellation == 'none':
        raise DeploymentModeError('objective_cancellation_conflict', 409)
    return incoming
