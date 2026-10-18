from typing import Annotated, Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PERSONAL_DEPLOYMENT_SCHEMA = 'magistrate.personal-deployment.v1'
OBJECTIVE_STATE_SCHEMA = 'magistrate.objective-state.v1'
MAX_CONTRACT_INTEGER = 9_007_199_254_740_991
PRINCIPAL_ID_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'


class PersonalDeploymentContract(BaseModel):
    """Server configuration, never a request-body grant or subscription row.

    Spend is denominated in micro-USD, not purchased credits. Both ceilings
    must be explicitly configured before personal execution can be admitted.
    """

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal['magistrate.personal-deployment.v1'] = PERSONAL_DEPLOYMENT_SCHEMA
    mode: Literal['public-saas', 'personal'] = 'public-saas'
    owner_principal_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=PRINCIPAL_ID_PATTERN,
    )
    max_implementation_workers: int = Field(default=1, ge=1, le=2)
    objective_spend_limit_micro_usd: Optional[int] = Field(
        default=None, ge=1, le=MAX_CONTRACT_INTEGER,
    )
    monthly_spend_limit_micro_usd: Optional[int] = Field(
        default=None, ge=1, le=MAX_CONTRACT_INTEGER,
    )

    @model_validator(mode='after')
    def validate_personal_settings(self) -> 'PersonalDeploymentContract':
        settings = (
            self.owner_principal_id, self.objective_spend_limit_micro_usd,
            self.monthly_spend_limit_micro_usd,
        )
        if self.mode == 'personal':
            if any(value is None for value in settings):
                raise ValueError('Personal mode requires an exact owner and explicit spend ceilings.')
            if self.objective_spend_limit_micro_usd > self.monthly_spend_limit_micro_usd:
                raise ValueError('The objective ceiling must fit inside the monthly ceiling.')
        elif any(value is not None for value in settings):
            raise ValueError('Personal settings cannot grant authority in public-SaaS mode.')
        return self


ObjectiveState = Literal[
    'unknown', 'submitting', 'queued', 'accepted', 'running', 'waiting_for_input',
    'recovering', 'succeeded', 'failed', 'cancelled',
]


class ObjectiveStateContract(BaseModel):
    """Versioned projection of durable facts, not a worker-control request.

    Revision comes from persisted authority, never wall-clock reconstruction.
    Unknown/recovering and cancellation-requested are nonterminal and retain
    capacity. Completion is supplied only after execution-evidence validation.
    """

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal['magistrate.objective-state.v1'] = OBJECTIVE_STATE_SCHEMA
    objective_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
    owner_principal_id: str = Field(min_length=1, max_length=128, pattern=PRINCIPAL_ID_PATTERN)
    revision: int = Field(ge=0, le=MAX_CONTRACT_INTEGER)
    state: ObjectiveState
    # Persist this high-water mark through unknown/recovering/input waits so a
    # later retry cannot forget acceptance or restart already-running work.
    progress_stage: Literal['unobserved', 'submitting', 'queued', 'accepted', 'running', 'terminal']
    cancellation: Literal['none', 'requested', 'observed'] = 'none'

    @model_validator(mode='after')
    def validate_state(self) -> 'ObjectiveStateContract':
        if (self.cancellation == 'observed') != (self.state == 'cancelled'):
            raise ValueError('Only an observed cancellation establishes cancelled state.')
        if self.terminal != (self.progress_stage == 'terminal'):
            raise ValueError('Terminal progress requires a terminal objective state.')
        if self.state in {'submitting', 'queued', 'accepted', 'running'} and self.progress_stage != self.state:
            raise ValueError('Observed progress must agree with the objective state.')
        return self

    @property
    def terminal(self) -> bool:
        return self.state in {'succeeded', 'failed', 'cancelled'}


# Additive personal-runtime contracts. v1 above remains the persisted compatibility
# projection; neither exporting v3 nor selecting this flag activates a producer.
PERSONAL_RUNTIME_FEATURE_FLAG = 'PERSONAL_RUNTIME_V2'
OBJECTIVE_STATE_V3_SCHEMA = 'magistrate.objective-state.v3'
DECISION_BINDING_V2_SCHEMA = 'firstmate.decision-binding.v2'
RUNTIME_ENVELOPE_V2_SCHEMA = 'firstmate.runtime-envelope.v2'
VOICE_SESSION_V1_SCHEMA = 'magistrate.voice-session.v1'
OpaqueId = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
SafeCounter = Annotated[int, Field(ge=0, le=MAX_CONTRACT_INTEGER)]
PositiveCounter = Annotated[int, Field(ge=1, le=MAX_CONTRACT_INTEGER)]


class _FoundationContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class ObjectiveIdentityV3(_FoundationContract):
    """Frozen at admission; retries/reconnects must reuse these exact identities.

    The store, not this value object, enforces uniqueness and owner binding.
    job_id names the durable dispatch, run_id the selected execution attempt.
    Neither may be derived from display text or regenerated after uncertainty.
    """
    owner_principal_id: str = Field(pattern=PRINCIPAL_ID_PATTERN)
    project_id: OpaqueId
    objective_id: OpaqueId
    job_id: OpaqueId
    run_id: OpaqueId
    conversation_id: str = Field(min_length=8, max_length=128, pattern=r'^mgc_[A-Za-z0-9_-]+$')
    user_message_id: str = Field(min_length=8, max_length=128, pattern=r'^mgm_[A-Za-z0-9_-]+$')
    idempotency_key: OpaqueId
    request_sha256: Sha256


class ReplayIdentityV3(_FoundationContract):
    """Authenticated producer identity plus event id is the immutable dedupe key.

    replay_cursor is an owner-scoped persisted ledger cursor, not a timestamp.
    Reusing a key with different bytes is a conflict, never a new event.
    """
    source_instance_id: OpaqueId
    source_event_id: OpaqueId
    event_sha256: Sha256
    replay_cursor: PositiveCounter


ObjectiveLifecycleV3 = Literal[
    'accepted', 'running', 'verifying', 'waiting_for_input', 'failed', 'cancelled', 'completed',
]


class ObjectiveStateV3(_FoundationContract):
    """Observed lifecycle plus independent dispatch uncertainty/cancellation facts.

    null state means no producer lifecycle has been observed; it must not render
    as accepted/running. Legacy succeeded maps to completed only at a write-side
    validated projection. The seven lifecycle values never imply hard limits.
    """
    schema_version: Literal['magistrate.objective-state.v3'] = OBJECTIVE_STATE_V3_SCHEMA
    identity: ObjectiveIdentityV3
    revision: PositiveCounter
    state: Optional[ObjectiveLifecycleV3] = None
    dispatch: Literal['pending', 'acknowledged', 'uncertain']
    cancellation: Literal['none', 'requested', 'acknowledged', 'observed'] = 'none'
    last_event: Optional[ReplayIdentityV3] = None
    completion_evidence_sha256: Optional[Sha256] = None

    @model_validator(mode='after')
    def observed_facts(self) -> 'ObjectiveStateV3':
        if self.state is not None and self.last_event is None:
            raise ValueError('Lifecycle state requires a persisted producer event.')
        if self.state is not None and self.dispatch != 'acknowledged':
            raise ValueError('Observed execution acknowledges dispatch; uncertainty is not execution.')
        if (self.state == 'cancelled') != (self.cancellation == 'observed'):
            raise ValueError('Cancellation is terminal only after observation.')
        if (self.state == 'completed') != (self.completion_evidence_sha256 is not None):
            raise ValueError('Completion requires validated, immutable verification evidence.')
        return self


class RepositoryRestrictionV2(_FoundationContract):
    """One exact authorized repository and immutable checkout, not a path/glob."""
    repository_id: PositiveCounter
    installation_id: PositiveCounter
    full_name: str = Field(pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', max_length=256)
    base_commit: str = Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')


class RuntimeSpendV2(_FoundationContract):
    """Admission reservations and measured cost are separate, integer micro-USD.

    unavailable/null never means free. A reservation is not measured usage or a
    runtime hard cap. Only an acknowledged producer can claim enforcement.
    """
    measurement: Literal['unavailable', 'partial', 'final']
    measured_micro_usd: Optional[SafeCounter] = None
    reserved_micro_usd: SafeCounter
    limit_micro_usd: PositiveCounter
    enforcement: Literal['unavailable', 'admission-only', 'runtime-hard-limit']
    evidence_event_id: Optional[OpaqueId] = None
    runtime_acknowledgment_event_id: Optional[OpaqueId] = None

    @model_validator(mode='after')
    def measured_facts(self) -> 'RuntimeSpendV2':
        if (self.measurement == 'unavailable') != (self.measured_micro_usd is None):
            raise ValueError('Unknown spend cannot be represented by zero or fabricated usage.')
        if self.measurement != 'unavailable' and self.evidence_event_id is None:
            raise ValueError('Measured spend requires producer evidence.')
        if self.enforcement == 'runtime-hard-limit' and self.runtime_acknowledgment_event_id is None:
            raise ValueError('A runtime spend limit requires an authenticated acknowledgment reference.')
        return self


class FirstmateRuntimeEnvelopeV2(_FoundationContract):
    """Frozen requested limits, NOT evidence that the selected runtime enforces them.

    The authenticated write-side admission seam must compare an acknowledgment
    of these exact bytes and the pinned producer before publishing work.
    """
    schema_version: Literal['firstmate.runtime-envelope.v2'] = RUNTIME_ENVELOPE_V2_SCHEMA
    feature: Literal['PERSONAL_RUNTIME_V2'] = PERSONAL_RUNTIME_FEATURE_FLAG
    identity: ObjectiveIdentityV3
    repository: RepositoryRestrictionV2
    max_implementation_workers: int = Field(ge=1, le=2)
    max_execution_seconds: int = Field(ge=1, le=86400)
    objective_spend_limit_micro_usd: PositiveCounter
    monthly_spend_limit_micro_usd: PositiveCounter
    acknowledgment_deadline_ms: PositiveCounter
    cancellation_grace_seconds: int = Field(ge=1, le=300)

    @model_validator(mode='after')
    def bounded_spend(self) -> 'FirstmateRuntimeEnvelopeV2':
        if self.objective_spend_limit_micro_usd > self.monthly_spend_limit_micro_usd:
            raise ValueError('Objective limit must fit inside the monthly limit.')
        return self


class FirstmateRuntimeAcknowledgmentV2(_FoundationContract):
    """Authenticated persisted receipt, never synthesized from operator config.

    Exact envelope digest, identities and producer pin must match at admission.
    A receipt declares only these capabilities; it is not sandbox certification.
    rejected/unsupported cannot carry an enforcement claim.
    """
    schema_version: Literal['firstmate.runtime-acknowledgment.v2'] = 'firstmate.runtime-acknowledgment.v2'
    identity: ObjectiveIdentityV3
    envelope_sha256: Sha256
    producer_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    event: ReplayIdentityV3
    acknowledged_at_ms: PositiveCounter
    status: Literal['acknowledged', 'rejected', 'unsupported']
    repository_enforcement: Literal['unavailable', 'enforced']
    concurrency_enforcement: Literal['unavailable', 'enforced']
    duration_enforcement: Literal['unavailable', 'enforced']
    cancellation_enforcement: Literal['unavailable', 'enforced']
    spend_enforcement: Literal['unavailable', 'admission-only', 'runtime-hard-limit']

    @model_validator(mode='after')
    def no_unacknowledged_capability(self) -> 'FirstmateRuntimeAcknowledgmentV2':
        if self.status != 'acknowledged' and any(value != 'unavailable' for value in (
            self.repository_enforcement, self.concurrency_enforcement,
            self.duration_enforcement, self.cancellation_enforcement, self.spend_enforcement,
        )):
            raise ValueError('A rejected/unsupported runtime cannot acknowledge enforcement.')
        return self


class DecisionBindingV2(_FoundationContract):
    """Exact revision and request authority; legacy unbound decisions stay read-only.

    No answer text, bearer or confirmation token is exported. Canonical answer
    bytes and current repository authorization must be loaded out of band.
    """
    schema_version: Literal['firstmate.decision-binding.v2'] = DECISION_BINDING_V2_SCHEMA
    identity: ObjectiveIdentityV3
    decision_id: str = Field(pattern=r'^fmd_[0-9a-f]{32}$')
    decision_revision: PositiveCounter
    request_id: OpaqueId
    scope: OpaqueId
    expires_at_ms: PositiveCounter

    @model_validator(mode='after')
    def exact_project_scope(self) -> 'DecisionBindingV2':
        if self.scope != self.identity.project_id:
            raise ValueError('Decision scope must equal the frozen authorized project.')
        return self


class DecisionRuntimeAcknowledgmentV2(_FoundationContract):
    schema_version: Literal['firstmate.decision-acknowledgment.v2'] = 'firstmate.decision-acknowledgment.v2'
    binding: DecisionBindingV2
    answer_user_message_id: str = Field(min_length=8, max_length=128, pattern=r'^mgm_[A-Za-z0-9_-]+$')
    answer_sha256: Sha256
    event: ReplayIdentityV3
    status: Literal['received', 'applied', 'rejected']
    # received is NOT worker continuation; only applied acknowledges application.
    acknowledged_at_ms: PositiveCounter


class VoiceSessionV1(_FoundationContract):
    """Owner/thread continuity without exporting provider or gateway credentials.

    session_generation fences revoked/replaced transport sessions. reconnects
    retain conversation/session identity; turn_id and client_message_id dedupe
    one canonical message across retries. Local microphone permission is not
    server authorization. Only a current authenticated receipt can say authorized.
    """
    schema_version: Literal['magistrate.voice-session.v1'] = VOICE_SESSION_V1_SCHEMA
    feature: Literal['PERSONAL_RUNTIME_V2'] = PERSONAL_RUNTIME_FEATURE_FLAG
    session_id: OpaqueId
    session_generation: PositiveCounter
    owner_principal_id: str = Field(pattern=PRINCIPAL_ID_PATTERN)
    conversation_id: str = Field(min_length=8, max_length=128, pattern=r'^mgc_[A-Za-z0-9_-]+$')
    connection_id: OpaqueId
    turn_id: Optional[OpaqueId] = None
    client_message_id: Optional[str] = Field(default=None, min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]+$')
    last_event_sequence: SafeCounter
    authorization: Literal['unavailable', 'authorized', 'expired', 'revoked']
    authorization_event_id: Optional[OpaqueId] = None
    expires_at_ms: Optional[PositiveCounter] = None
    transport: Literal['unavailable', 'connecting', 'connected', 'reconnecting', 'closed']

    @model_validator(mode='after')
    def authorized_continuity(self) -> 'VoiceSessionV1':
        if (self.turn_id is None) != (self.client_message_id is None):
            raise ValueError('A voice turn and canonical message dedupe key must be bound together.')
        if self.authorization == 'authorized' and (self.authorization_event_id is None or self.expires_at_ms is None):
            raise ValueError('Voice authorization requires an expiring authenticated receipt.')
        if self.transport in {'connecting', 'connected', 'reconnecting'} and self.authorization != 'authorized':
            raise ValueError('A live voice transport requires current authorization.')
        return self


class UploadedAttachmentContract(BaseModel):
    upload_id: str = Field(min_length=16, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    filename: str = Field(min_length=1, max_length=160)
    media_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0, le=25 * 1024 * 1024)

# Retained as the bounded Activity catch-up request envelope size.
MAGI_MAX_RESPONSE_BYTES = 256 * 1024


class ActivityCatchUpContract(BaseModel):
    """Bounded body form of the activity replay endpoint."""

    model_config = ConfigDict(extra='forbid', strict=True)
    after: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    limit: int = Field(default=100, ge=1, le=200)
    reconcile: bool = True


class NativeMagiMessageContract(BaseModel):
    """Authenticated native-chat submission; ownership comes only from auth."""

    model_config = ConfigDict(extra='forbid', strict=True)
    conversation_id: Optional[str] = Field(
        default=None, min_length=8, max_length=128,
        pattern=r'^mgc_[A-Za-z0-9_-]+$',
    )
    client_message_id: str = Field(
        min_length=8, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]+$',
    )
    content: str = Field(min_length=1, max_length=100_000)
    source: Literal['text', 'voice'] = 'text'
    attachments: List[UploadedAttachmentContract] = Field(default_factory=list, max_length=10)
    retry_failed: bool = False
    # Required for host-classified production/data/credential/spend/release
    # actions. It authorizes only this new idempotent submission; it is never
    # inferred from conversational prose or reused from an earlier message.
    explicit_confirmation: bool = False

    @field_validator('content')
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('A native Magi message cannot be blank.')
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError('A native Magi message must contain valid Unicode scalar values.')
        return value


class ExecutionSettingsContract(BaseModel):
    profile_id: Optional[str] = Field(default=None, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    routing_profile_id: Optional[str] = Field(default=None, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    switching_behavior: Optional[Literal['migrate', 'new-session']] = None
    unavailable_behavior: Optional[Literal['error', 'fallback']] = None


class ExecutionRouteRequirementsContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reasoning: int = Field(default=1, ge=1, le=5)
    context_tokens: int = Field(default=0, ge=0, le=10_000_000)
    tools: bool = True
    multimodal: bool = False
    max_latency_ms: Optional[int] = Field(default=None, ge=1, le=300_000)
    preferred_profile_id: Optional[str] = Field(
        default=None, max_length=128,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$',
    )


class RoutingPreferenceContract(BaseModel):
    harness: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    model: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')


class AgentMigrationRequestContract(BaseModel):
    profile_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    confirmed: Literal[True]


class AgentMigrationTransitionContract(BaseModel):
    state: Literal['relaunching', 'running-on-new', 'failed']
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    terminal_confirmed: Literal[True]
    evidence: str = Field(min_length=1, max_length=2000)

class ExecutionCredentialContract(BaseModel):
    credential: str = Field(min_length=1, max_length=10000)

class GestureInputContract(BaseModel):
    action: str
    target_id: Optional[str] = None

class NotificationAckContract(BaseModel):
    item_ids: List[str]

class AttentionActionContract(BaseModel):
    action_key: str = Field(min_length=12, max_length=128, pattern=r'^aa1_[A-Za-z0-9]+$')
    action: Literal['approve', 'reject']
    target_id: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9._:-]+$')

class AttentionActionExecuteContract(AttentionActionContract):
    confirmation_token: str = Field(min_length=16, max_length=128)

class NotificationPreferencesContract(BaseModel):
    enabled: bool = True
    quiet_start: Optional[int] = None
    quiet_end: Optional[int] = None
    mode: Literal['restricted', 'moderate', 'full'] = 'moderate'

class RenameAgentContract(BaseModel):
    name: str = Field(min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_-]{0,31}$')
