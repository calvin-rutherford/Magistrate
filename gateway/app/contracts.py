from typing import Literal, List, Optional

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
