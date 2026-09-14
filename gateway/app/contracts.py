from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
