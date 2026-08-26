from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal, List

class UniversalInputContract(BaseModel):
    source: str = 'iphone'
    modality: str = 'text'
    type: str = 'prompt'
    text: Optional[str] = None
    target: str = 'captain'

class GestureInputContract(BaseModel):
    action: str
    target_id: Optional[str] = None

class NotificationAckContract(BaseModel):
    item_ids: List[str]

class NotificationPreferencesContract(BaseModel):
    enabled: bool = True
    quiet_start: Optional[int] = None
    quiet_end: Optional[int] = None

class VoiceImpact(str, Enum):
    READ = 'read'
    NAVIGATE = 'navigate'
    PROMPT = 'prompt'
    CONTROL = 'control'
    PROHIBITED = 'prohibited'

class VoiceMoveRequest(BaseModel):
    schema_version: Literal['voice-move.v1'] = 'voice-move.v1'
    utterance: str = Field(min_length=1, max_length=4000)
    target: str = Field(default='captain', min_length=1, max_length=200)
    source: str = Field(default='voice-page', max_length=50)
    session_id: str = Field(min_length=16, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=200)
    execute: bool = False
    confirmation_token: Optional[str] = None

    @field_validator('utterance', 'target', 'source', 'session_id', 'idempotency_key', 'confirmation_token', mode='before')
    @classmethod
    def trim_text(cls, value):
        if value is None:
            return value
        if not isinstance(value, str):
            raise TypeError('must be a string')
        trimmed = value.strip()
        if not trimmed:
            raise ValueError('must not be blank')
        return trimmed

    @field_validator('idempotency_key')
    @classmethod
    def safe_idempotency_key(cls, value: str) -> str:
        if not all(char.isalnum() or char in '-_:.' for char in value):
            raise ValueError("must contain only letters, numbers, '.', '_', ':', or '-'")
        return value


class VoiceMoveCancelRequest(BaseModel):
    session_id: str = Field(min_length=16, max_length=120)

    @field_validator('session_id', mode='before')
    @classmethod
    def trim_session(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('session_id must not be blank')
        return value.strip()
