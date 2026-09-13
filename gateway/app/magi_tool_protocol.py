"""Closed tool boundary for provider-native Magi chat.

Model output crosses this boundary only as a named call with raw JSON arguments.
The concrete executor validates the arguments and derives tenant identity from
``MagiToolContext``; model JSON can never select a principal or canonical chat
identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.magi_model import MagiModelToolCall, MagiToolDefinition


@dataclass(frozen=True)
class MagiToolContext:
    """Server-authored identity for one model tool invocation."""

    owner_user_id: str
    conversation_id: str
    turn_id: str
    user_message_id: str
    assistant_message_id: str
    command_authorized: bool


@dataclass(frozen=True)
class MagiToolExecutionResult:
    """Bounded structured result returned to the model, never directly to chat."""

    payload: Mapping[str, Any]

    def model_content(self) -> str:
        return json.dumps(
            dict(self.payload), ensure_ascii=False, separators=(",", ":"),
            sort_keys=True, allow_nan=False,
        )


class MagiToolError(RuntimeError):
    """Content-free tool failure safe for native-chat error classification."""

    def __init__(self, code: str, *, retryable: bool = True):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@runtime_checkable
class MagiToolExecutor(Protocol):
    """Least-privilege executor injected into the otherwise generic chat service."""

    @property
    def definitions(self) -> Sequence[MagiToolDefinition]:
        ...

    async def execute(
        self,
        call: MagiModelToolCall,
        *,
        context: MagiToolContext,
        invocation_key: str,
    ) -> MagiToolExecutionResult:
        ...
