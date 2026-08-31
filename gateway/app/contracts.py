from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Literal, List

class UploadedAttachmentContract(BaseModel):
    upload_id: str = Field(min_length=16, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    filename: str = Field(min_length=1, max_length=160)
    media_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0, le=25 * 1024 * 1024)

class UniversalInputContract(BaseModel):
    source: str = 'iphone'
    modality: str = 'text'
    type: str = 'prompt'
    text: Optional[str] = Field(default=None, max_length=100_000)
    target: str = Field(default='captain', min_length=1, max_length=200)
    message_id: Optional[str] = Field(default=None, min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    harness: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    provider: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    model: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    variant: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    profile_id: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    attachments: Optional[List[UploadedAttachmentContract]] = Field(default=None, max_length=10)

class ExecutionSettingsContract(BaseModel):
    profile_id: Optional[str] = Field(default=None, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
    switching_behavior: Optional[Literal['migrate', 'new-session']] = None
    unavailable_behavior: Optional[Literal['error', 'fallback']] = None

class ExecutionCredentialContract(BaseModel):
    credential: str = Field(min_length=1, max_length=10000)

class GestureInputContract(BaseModel):
    action: str
    target_id: Optional[str] = None

class NotificationAckContract(BaseModel):
    item_ids: List[str]

class NotificationPreferencesContract(BaseModel):
    enabled: bool = True
    quiet_start: Optional[int] = None
    quiet_end: Optional[int] = None
    mode: Literal['restricted', 'moderate', 'full'] = 'moderate'

class RenameAgentContract(BaseModel):
    name: str = Field(min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_-]{0,31}$')

class VoiceImpact(str, Enum):
    READ = 'read'
    PROMPT = 'prompt'
    CONTROL = 'control'
    PROHIBITED = 'prohibited'

class VoiceMoveRequest(BaseModel):
    schema_version: Literal['voice-move.v1'] = 'voice-move.v1'
    utterance: str = Field(min_length=1, max_length=4000)
    target: str = Field(default='captain', min_length=1, max_length=200)
    source: str = Field(default='voice-page', max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=200)
    execute: bool = False
    confirmation_token: Optional[str] = None
