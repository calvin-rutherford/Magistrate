from enum import Enum
import json
from typing import Annotated, Literal, List, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class UploadedAttachmentContract(BaseModel):
    upload_id: str = Field(min_length=16, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    filename: str = Field(min_length=1, max_length=160)
    media_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0, le=25 * 1024 * 1024)

# These bounds are duplicated by the independent frontend validator. The
# Gateway remains authoritative; the client repeats the checks because HTTP,
# WebSocket, and cached JSON are all untrusted at render time.
MAGI_RESPONSE_SCHEMA = 'magi.response.v1'
MAGI_EVENT_SCHEMA = 'magi.event.v1'
MAGI_MAX_RESPONSE_BYTES = 256 * 1024
MAGI_MAX_TEXT_CHARS = 200_000
MAGI_MAX_BLOCKS = 128
MAGI_MAX_INLINE_NODES = 128
MAGI_MAX_TOTAL_INLINE_NODES = 1024
MAGI_MAX_LIST_ITEMS = 100
MAGI_MAX_ACTIONS = 16
_MAGI_ID_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
_MAGI_LANGUAGE_PATTERN = r'^[A-Za-z0-9_+.#-]{0,32}$'


class _StrictMagiContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*', mode='after')
    @classmethod
    def validate_unicode_scalars(cls, value):
        # Python's JSON decoder can preserve an unpaired UTF-16 surrogate from
        # a `\ud800` escape. Accepting it would make a later UTF-8 database/hash
        # serialization fail after contract validation instead of returning a
        # controlled 422.
        if isinstance(value, str) and any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError('Structured response strings must contain valid Unicode scalar values.')
        return value


class MagiTextNode(_StrictMagiContract):
    type: Literal['text']
    text: str = Field(min_length=1, max_length=16_384)


class MagiStrongNode(_StrictMagiContract):
    type: Literal['strong']
    text: str = Field(min_length=1, max_length=16_384)


class MagiEmphasisNode(_StrictMagiContract):
    type: Literal['emphasis']
    text: str = Field(min_length=1, max_length=16_384)


class MagiInlineCodeNode(_StrictMagiContract):
    type: Literal['inline_code']
    text: str = Field(min_length=1, max_length=16_384)


def _validated_magi_url(value: str) -> str:
    """Accept only bounded, absolute HTTP(S) links with no credentials.

    Structured content is rendered as native text, never as HTML. Restricting
    links here still matters because a press leaves the app. Backslashes,
    controls, and every whitespace code point are rejected before URL parsing
    so browser normalization cannot reinterpret an ambiguous value.
    """
    if (
        not value
        or len(value) > 2048
        or value != value.strip()
        or '\\' in value
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError('Links must be bounded absolute HTTP(S) URLs.')
    try:
        parsed = urlsplit(value)
        # Accessing port performs urllib's strict port validation.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError('Links must be bounded absolute HTTP(S) URLs.') from exc
    if (
        parsed.scheme.lower() not in {'http', 'https'}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError('Links must be bounded absolute HTTP(S) URLs.')
    return value


class MagiLinkNode(_StrictMagiContract):
    type: Literal['link']
    text: str = Field(min_length=1, max_length=4096)
    url: str = Field(min_length=1, max_length=2048)

    _safe_url = field_validator('url')(_validated_magi_url)


MagiInlineNode = Annotated[
    Union[MagiTextNode, MagiStrongNode, MagiEmphasisNode, MagiInlineCodeNode, MagiLinkNode],
    Field(discriminator='type'),
]


class MagiParagraphBlock(_StrictMagiContract):
    type: Literal['paragraph']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    content: List[MagiInlineNode] = Field(min_length=1, max_length=MAGI_MAX_INLINE_NODES)


class MagiHeadingBlock(_StrictMagiContract):
    type: Literal['heading']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    level: int = Field(ge=1, le=4)
    content: List[MagiInlineNode] = Field(min_length=1, max_length=MAGI_MAX_INLINE_NODES)


class MagiListBlock(_StrictMagiContract):
    type: Literal['list']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    style: Literal['ordered', 'unordered']
    items: List[List[MagiInlineNode]] = Field(min_length=1, max_length=MAGI_MAX_LIST_ITEMS)

    @field_validator('items')
    @classmethod
    def validate_items(cls, items: List[List[MagiInlineNode]]) -> List[List[MagiInlineNode]]:
        if any(not item or len(item) > MAGI_MAX_INLINE_NODES for item in items):
            raise ValueError('Every list item needs a bounded inline-node list.')
        if sum(len(item) for item in items) > MAGI_MAX_TOTAL_INLINE_NODES:
            raise ValueError('A list contains too many inline nodes.')
        return items


class MagiCodeBlock(_StrictMagiContract):
    type: Literal['code']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    code: str = Field(min_length=1, max_length=65_536)
    language: Optional[str] = Field(default=None, max_length=32, pattern=_MAGI_LANGUAGE_PATTERN)


class MagiQuoteBlock(_StrictMagiContract):
    type: Literal['quote']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    content: List[MagiInlineNode] = Field(min_length=1, max_length=MAGI_MAX_INLINE_NODES)


class MagiDividerBlock(_StrictMagiContract):
    type: Literal['divider']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)


MagiResponseBlock = Annotated[
    Union[MagiParagraphBlock, MagiHeadingBlock, MagiListBlock, MagiCodeBlock, MagiQuoteBlock, MagiDividerBlock],
    Field(discriminator='type'),
]


class MagiOpenUrlAction(_StrictMagiContract):
    """Reserved typed data. Receiving it never executes the action."""

    type: Literal['open_url']
    action_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)

    _safe_url = field_validator('url')(_validated_magi_url)


class MagiResponseV1(_StrictMagiContract):
    schema_version: Literal['magi.response.v1']
    blocks: List[MagiResponseBlock] = Field(min_length=1, max_length=MAGI_MAX_BLOCKS)
    actions: List[MagiOpenUrlAction] = Field(default_factory=list, max_length=MAGI_MAX_ACTIONS)

    @model_validator(mode='after')
    def validate_document_bounds(self) -> 'MagiResponseV1':
        block_ids = [block.block_id for block in self.blocks]
        action_ids = [action.action_id for action in self.actions]
        if len(set(block_ids)) != len(block_ids):
            raise ValueError('Structured response block ids must be unique.')
        if len(set(action_ids)) != len(action_ids):
            raise ValueError('Structured response action ids must be unique.')
        text_chars = 0
        inline_nodes = 0
        for block in self.blocks:
            if isinstance(block, MagiCodeBlock):
                text_chars += len(block.code)
                continue
            if isinstance(block, MagiDividerBlock):
                continue
            groups = block.items if isinstance(block, MagiListBlock) else [block.content]
            for group in groups:
                inline_nodes += len(group)
                for node in group:
                    text_chars += len(node.text)
                    if isinstance(node, MagiLinkNode):
                        text_chars += len(node.url)
        for action in self.actions:
            text_chars += len(action.label) + len(action.url)
        if inline_nodes > MAGI_MAX_TOTAL_INLINE_NODES:
            raise ValueError('Structured response contains too many inline nodes.')
        if text_chars > MAGI_MAX_TEXT_CHARS:
            raise ValueError('Structured response text is too large.')
        encoded = json.dumps(
            self.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':'), sort_keys=True,
        ).encode('utf-8')
        if len(encoded) > MAGI_MAX_RESPONSE_BYTES:
            raise ValueError('Structured response document is too large.')
        return self


def _magi_inline_plain_text(nodes: List[MagiInlineNode]) -> str:
    parts: List[str] = []
    for node in nodes:
        if isinstance(node, MagiLinkNode) and node.text != node.url:
            parts.append(f'{node.text} ({node.url})')
        else:
            parts.append(node.text)
    return ''.join(parts)


def magi_response_plain_text(response: MagiResponseV1) -> str:
    """Derive accessibility/legacy plain text without producing Markdown."""
    sections: List[str] = []
    for block in response.blocks:
        if isinstance(block, MagiCodeBlock):
            sections.append(block.code)
        elif isinstance(block, MagiDividerBlock):
            sections.append('────────')
        elif isinstance(block, MagiListBlock):
            lines = []
            for index, item in enumerate(block.items):
                marker = f'{index + 1}.' if block.style == 'ordered' else '•'
                lines.append(f'{marker} {_magi_inline_plain_text(item)}')
            sections.append('\n'.join(lines))
        else:
            sections.append(_magi_inline_plain_text(block.content))
    return '\n\n'.join(section for section in sections if section).strip()


class _MagiEventBase(_StrictMagiContract):
    schema_version: Literal['magi.event.v1']
    event_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)
    turn_id: str = Field(min_length=4, max_length=128, pattern=r'^ct_[A-Za-z0-9_-]+$')
    message_id: str = Field(min_length=4, max_length=128, pattern=r'^cm_[A-Za-z0-9_-]+$')
    revision: int = Field(ge=1, le=2_147_483_647)

    @model_validator(mode='after')
    def validate_event_size(self) -> '_MagiEventBase':
        encoded = json.dumps(
            self.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':'), sort_keys=True,
        ).encode('utf-8')
        if len(encoded) > MAGI_MAX_RESPONSE_BYTES:
            raise ValueError('Structured response event is too large.')
        return self


class MagiAssistantStartedEvent(_MagiEventBase):
    event_type: Literal['assistant.started']


class MagiAssistantBlockUpsertEvent(_MagiEventBase):
    event_type: Literal['assistant.block.upsert']
    block: MagiResponseBlock
    block_index: int = Field(ge=0, lt=MAGI_MAX_BLOCKS)


class MagiAssistantBlockRemoveEvent(_MagiEventBase):
    """An explicit producer correction; omission never means deletion."""

    event_type: Literal['assistant.block.remove']
    block_id: str = Field(min_length=1, max_length=128, pattern=_MAGI_ID_PATTERN)


class MagiAssistantCompletedEvent(_MagiEventBase):
    event_type: Literal['assistant.completed']
    response: MagiResponseV1


class MagiAssistantFailedEvent(_MagiEventBase):
    event_type: Literal['assistant.failed']
    error_code: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9._-]{0,63}$')
    error_message: Optional[str] = Field(default=None, max_length=1000)


class MagiAssistantCancelledEvent(_MagiEventBase):
    event_type: Literal['assistant.cancelled']


MagiEventContract = Annotated[
    Union[
        MagiAssistantStartedEvent,
        MagiAssistantBlockUpsertEvent,
        MagiAssistantBlockRemoveEvent,
        MagiAssistantCompletedEvent,
        MagiAssistantFailedEvent,
        MagiAssistantCancelledEvent,
    ],
    Field(discriminator='event_type'),
]


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
    # The client's submission identity for this utterance, so a voice turn is
    # recorded in the canonical conversation exactly like a typed one.
    client_message_id: Optional[str] = Field(default=None, min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    execute: bool = False
    confirmation_token: Optional[str] = None
