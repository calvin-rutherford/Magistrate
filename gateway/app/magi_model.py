"""Provider-independent model boundary for native Magi chat.

Complete user-visible text and closed, bounded function-call envelopes can cross
this module. Prompt/response content is never logged, and provider reasoning or
unknown protocol payloads are never returned as prose.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit

import httpx

MAGI_MAX_RESPONSE_CHARACTERS = 200_000
MAGI_MAX_RESPONSE_BYTES = 256 * 1024
MAGI_DEFAULT_MAX_OUTPUT_TOKENS = 16_384
MAGI_MAX_TOOL_CALLS = 4
MAGI_MAX_TOOL_DEFINITIONS = 5
MAGI_MAX_TOOL_ARGUMENT_BYTES = 32 * 1024
MAGI_MAX_TOOL_DEFINITION_BYTES = 64 * 1024
_MAGI_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_OPENAI_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def magi_text_has_unsafe_controls(value: str) -> bool:
    return any(
        (code < 32 and code not in {9, 10, 13})
        or 127 <= code <= 159
        or 0xD800 <= code <= 0xDFFF
        for code in map(ord, value)
    )


@dataclass(frozen=True)
class MagiModelToolCall:
    """One provider function call; arguments remain raw until tool validation."""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class MagiToolDefinition:
    """Closed function definition offered by the host, never by request JSON."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class MagiModelMessage:
    role: str
    content: str | None
    tool_calls: tuple[MagiModelToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class MagiModelResult:
    """A complete provider turn; final text is preserved exactly as received."""

    text: str | None
    finish_reason: str = "stop"
    tool_calls: tuple[MagiModelToolCall, ...] = ()


class MagiModelError(RuntimeError):
    """Safe, content-free provider failure suitable for service classification."""

    def __init__(self, code: str, *, retryable: bool = True, tool_calls: int = 0):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.tool_calls = max(0, int(tool_calls))


@runtime_checkable
class MagiModel(Protocol):
    """One asynchronous, non-streaming model completion interface."""

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        ...


def _validated_utf8(value: str, *, maximum_bytes: int, code: str) -> bytes:
    if magi_text_has_unsafe_controls(value):
        raise MagiModelError(code)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise MagiModelError(code) from exc
    if len(encoded) > maximum_bytes:
        raise MagiModelError(code)
    return encoded


def _openai_tool_name(canonical_name: str) -> str:
    """Encode a namespaced host name into OpenAI's documented name alphabet."""
    provider_name = canonical_name.replace(".", "__")
    if not _OPENAI_TOOL_NAME.fullmatch(provider_name):
        raise MagiModelError("provider_invalid_tool_definition", retryable=False)
    return provider_name


def _validated_tool_call(call: MagiModelToolCall) -> dict[str, Any]:
    if (
        not isinstance(call, MagiModelToolCall)
        or not isinstance(call.id, str) or not _TOOL_CALL_ID.fullmatch(call.id)
        or not isinstance(call.name, str) or not _TOOL_NAME.fullmatch(call.name)
        or not isinstance(call.arguments_json, str) or not call.arguments_json.strip()
    ):
        raise MagiModelError("provider_invalid_tool_call", retryable=False, tool_calls=1)
    _validated_utf8(
        call.arguments_json,
        maximum_bytes=MAGI_MAX_TOOL_ARGUMENT_BYTES,
        code="provider_invalid_tool_call",
    )
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": _openai_tool_name(call.name),
            "arguments": call.arguments_json,
        },
    }


def _response_input(messages: Sequence[MagiModelMessage]) -> list[dict[str, Any]]:
    """Encode the internal transcript as Responses input items.

    Responses does not use Chat Completions' assistant ``tool_calls`` and
    ``tool`` roles. Function calls and their results are separate input items,
    so keeping this conversion here prevents either protocol from leaking into
    the service boundary.
    """
    items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, MagiModelMessage):
            raise MagiModelError("provider_invalid_request", retryable=False)
        if message.role == "tool":
            if (
                message.tool_calls
                or not isinstance(message.tool_call_id, str)
                or not _TOOL_CALL_ID.fullmatch(message.tool_call_id)
                or not isinstance(message.content, str)
                or not message.content
            ):
                raise MagiModelError("provider_invalid_request", retryable=False)
            _validated_utf8(
                message.content,
                maximum_bytes=MAGI_MAX_TOOL_ARGUMENT_BYTES,
                code="provider_invalid_request",
            )
            items.append({
                "type": "function_call_output",
                "call_id": message.tool_call_id,
                "output": message.content,
            })
            continue
        if message.role not in {"user", "assistant"} or message.tool_call_id is not None:
            raise MagiModelError("provider_invalid_request", retryable=False)
        if message.tool_calls:
            if message.role != "assistant" or len(message.tool_calls) > MAGI_MAX_TOOL_CALLS:
                raise MagiModelError("provider_invalid_request", retryable=False)
            if message.content is not None:
                if not isinstance(message.content, str):
                    raise MagiModelError("provider_invalid_request", retryable=False)
                _validated_utf8(
                    message.content,
                    maximum_bytes=MAGI_MAX_RESPONSE_BYTES,
                    code="provider_invalid_request",
                )
                if message.content:
                    items.append({
                        "role": "assistant", "content": [
                            {"type": "output_text", "text": message.content},
                        ],
                    })
            for call in message.tool_calls:
                encoded = _validated_tool_call(call)
                items.append({
                    "type": "function_call",
                    "call_id": encoded["id"],
                    "name": encoded["function"]["name"],
                    "arguments": encoded["function"]["arguments"],
                })
            continue
        if not isinstance(message.content, str):
            raise MagiModelError("provider_invalid_request", retryable=False)
        _validated_utf8(
            message.content,
            maximum_bytes=MAGI_MAX_RESPONSE_BYTES,
            code="provider_invalid_request",
        )
        items.append({"role": message.role, "content": message.content})
    return items


def _provider_tools(tools: Sequence[MagiToolDefinition]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if len(tools) > MAGI_MAX_TOOL_DEFINITIONS:
        raise MagiModelError("provider_invalid_tool_definition", retryable=False)
    payloads: list[dict[str, Any]] = []
    names: set[str] = set()
    provider_names: dict[str, str] = {}
    for tool in tools:
        if (
            not isinstance(tool, MagiToolDefinition)
            or not isinstance(tool.name, str) or not _TOOL_NAME.fullmatch(tool.name)
            or tool.name in names
            or not isinstance(tool.description, str) or not tool.description.strip()
            or len(tool.description) > 1024
            or magi_text_has_unsafe_controls(tool.description)
            or not isinstance(tool.parameters, Mapping)
            or tool.parameters.get("type") != "object"
        ):
            raise MagiModelError("provider_invalid_tool_definition", retryable=False)
        try:
            encoded = json.dumps(
                dict(tool.parameters), ensure_ascii=False, separators=(",", ":"),
                sort_keys=True, allow_nan=False,
            ).encode("utf-8", errors="strict")
            parameters = json.loads(encoded)
        except (TypeError, ValueError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise MagiModelError("provider_invalid_tool_definition", retryable=False) from exc
        if len(encoded) > MAGI_MAX_TOOL_DEFINITION_BYTES:
            raise MagiModelError("provider_invalid_tool_definition", retryable=False)
        provider_name = _openai_tool_name(tool.name)
        if provider_name in provider_names:
            raise MagiModelError("provider_invalid_tool_definition", retryable=False)
        names.add(tool.name)
        provider_names[provider_name] = tool.name
        payloads.append({
            "type": "function",
            "name": provider_name,
            "description": tool.description,
            "parameters": parameters,
            "strict": True,
        })
    return payloads, provider_names


def _parse_response_tool_calls(
    raw_calls: list[Any],
    provider_names: Mapping[str, str],
) -> tuple[MagiModelToolCall, ...]:
    if not 1 <= len(raw_calls) <= MAGI_MAX_TOOL_CALLS:
        raise MagiModelError(
            "provider_invalid_tool_call", retryable=False, tool_calls=len(raw_calls),
        )
    parsed: list[MagiModelToolCall] = []
    seen: set[str] = set()
    for raw in raw_calls:
        if (
            not isinstance(raw, dict)
            or raw.get("type") != "function_call"
            or not isinstance(raw.get("call_id"), str)
            or not isinstance(raw.get("name"), str)
            or not isinstance(raw.get("arguments"), str)
        ):
            raise MagiModelError(
                "provider_invalid_tool_call", retryable=False, tool_calls=len(raw_calls),
            )
        provider_name = raw["name"]
        if provider_name not in provider_names:
            raise MagiModelError(
                "provider_unknown_tool_call", retryable=False, tool_calls=len(raw_calls),
            )
        call = MagiModelToolCall(
            id=raw["call_id"],
            name=provider_names[provider_name],
            arguments_json=raw["arguments"],
        )
        _validated_tool_call(call)
        if call.id in seen:
            raise MagiModelError(
                "provider_invalid_tool_call", retryable=False, tool_calls=len(raw_calls),
            )
        seen.add(call.id)
        parsed.append(call)
    return tuple(parsed)


def _response_text(output: list[Any]) -> tuple[str | None, list[Any]]:
    """Extract only assistant output text and function calls from Responses."""
    text_parts: list[str] = []
    calls: list[Any] = []
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise MagiModelError("provider_invalid_response", retryable=True)
        item_type = item["type"]
        if item_type == "function_call":
            calls.append(item)
            continue
        if item_type == "reasoning":
            # Reasoning is provider-private and must never become chat prose.
            continue
        if item_type != "message" or item.get("role") != "assistant":
            raise MagiModelError("provider_invalid_response", retryable=True)
        content = item.get("content")
        if not isinstance(content, list):
            raise MagiModelError("provider_invalid_response", retryable=True)
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise MagiModelError("provider_invalid_response", retryable=True)
            text = part.get("text")
            if not isinstance(text, str):
                raise MagiModelError("provider_invalid_response", retryable=True)
            text_parts.append(text)
    text = "".join(text_parts) if text_parts else None
    if text is not None:
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise MagiModelError("provider_invalid_unicode", retryable=True) from exc
        if len(text) > MAGI_MAX_RESPONSE_CHARACTERS or len(encoded) > MAGI_MAX_RESPONSE_BYTES:
            raise MagiModelError("response_too_large", retryable=True)
    return text, calls


class OpenAIMagiModel:
    """Concrete OpenAI Responses provider for native Magi chat.

    The existing deployment already supports server-side ``OPENAI_API_KEY`` and
    ``OPENAI_BASE_URL`` for speech transcription. Native chat uses the same
    server-only credential boundary, with an independently configured model.
    A provider ``length`` stop is an error: partial text is never persisted as a
    completed answer.
    """

    provider_id = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = (model or os.getenv("MAGISTRATE_MAGI_MODEL", "gpt-4o-mini")).strip()
        self._base_url = (base_url or os.getenv(
            "MAGISTRATE_MAGI_PROVIDER_URL",
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )).rstrip("/")
        self._max_output_tokens = max_output_tokens if max_output_tokens is not None else self._configured_max_tokens()
        configured_effort = reasoning_effort if reasoning_effort is not None else os.getenv(
            "MAGISTRATE_MAGI_REASONING_EFFORT", "",
        ).strip().lower()
        if configured_effort and configured_effort not in _MAGI_REASONING_EFFORTS:
            raise RuntimeError(
                "MAGISTRATE_MAGI_REASONING_EFFORT must be minimal, low, medium, or high"
            )
        # An omitted effort preserves the provider/model default (including
        # reasoning for GPT-5) and remains compatible with non-reasoning models.
        self._reasoning_effort = configured_effort or None
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else self._configured_timeout()
        self._transport = transport
        if (not self.model or len(self.model) > 128
                or any(not (character.isalnum() or character in "._:/-") for character in self.model)):
            raise RuntimeError("MAGISTRATE_MAGI_MODEL is invalid")
        provider_url = urlsplit(self._base_url)
        if (provider_url.scheme != "https" or not provider_url.hostname
                or provider_url.username is not None or provider_url.password is not None
                or provider_url.query or provider_url.fragment):
            raise RuntimeError("MAGISTRATE_MAGI_PROVIDER_URL must be a credential-free HTTPS URL")
        if not 1 <= self._max_output_tokens <= 65_536:
            raise RuntimeError("MAGISTRATE_MAGI_MAX_OUTPUT_TOKENS must be between 1 and 65536")
        if not 1 <= self._timeout_seconds <= 300:
            raise RuntimeError("MAGISTRATE_MAGI_TIMEOUT_SECONDS must be between 1 and 300")

    @staticmethod
    def _configured_max_tokens() -> int:
        try:
            return int(os.getenv("MAGISTRATE_MAGI_MAX_OUTPUT_TOKENS", str(MAGI_DEFAULT_MAX_OUTPUT_TOKENS)))
        except ValueError as exc:
            raise RuntimeError("MAGISTRATE_MAGI_MAX_OUTPUT_TOKENS must be an integer") from exc

    @staticmethod
    def _configured_timeout() -> float:
        try:
            return float(os.getenv("MAGISTRATE_MAGI_TIMEOUT_SECONDS", "120"))
        except ValueError as exc:
            raise RuntimeError("MAGISTRATE_MAGI_TIMEOUT_SECONDS must be numeric") from exc

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        if not self._api_key:
            raise MagiModelError("provider_not_configured", retryable=True)
        tool_payloads, provider_tool_names = _provider_tools(tools)
        _validated_utf8(
            system_context,
            maximum_bytes=MAGI_MAX_RESPONSE_BYTES,
            code="provider_invalid_request",
        )
        # The stable idempotency key lets a provider that supports that standard
        # header collapse a transport retry without exposing the client id.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Idempotency-Key": request_id,
        }
        request_payload: dict[str, Any] = {
            "model": self.model,
            "instructions": system_context,
            "input": _response_input(messages),
            "max_output_tokens": self._max_output_tokens,
            "stream": False,
        }
        # Do not force a Chat Completions-compatible reasoning workaround. The
        # Responses contract supports the model's native reasoning effort.
        if self._reasoning_effort:
            request_payload["reasoning"] = {"effort": self._reasoning_effort}
        if tool_payloads:
            request_payload.update({
                "tools": tool_payloads,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            })
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds, connect=min(10.0, self._timeout_seconds)),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/responses",
                    headers=headers,
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            raise MagiModelError("provider_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise MagiModelError("provider_unavailable", retryable=True) from exc
        if response.status_code >= 400:
            # Provider bodies can echo request material. Deliberately classify
            # only the status and never propagate or log the body.
            retryable = response.status_code == 429 or response.status_code >= 500
            raise MagiModelError("provider_rejected_request", retryable=retryable)
        try:
            payload = response.json()
            status = payload["status"]
            output = payload["output"]
            if not isinstance(status, str) or not isinstance(output, list):
                raise TypeError
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise MagiModelError("provider_invalid_response", retryable=True) from exc
        if status != "completed":
            incomplete = payload.get("incomplete_details")
            if (
                status == "incomplete" and isinstance(incomplete, dict)
                and incomplete.get("reason") == "max_output_tokens"
            ):
                raise MagiModelError("response_limit_reached", retryable=True)
            raise MagiModelError("provider_incomplete_response", retryable=True)
        try:
            text, raw_calls = _response_text(output)
        except MagiModelError:
            raise
        if raw_calls:
            if not tool_payloads:
                raise MagiModelError(
                    "provider_tool_call_unsupported", retryable=False, tool_calls=len(raw_calls),
                )
            calls = _parse_response_tool_calls(raw_calls, provider_tool_names)
            return MagiModelResult(text=text, finish_reason="tool_calls", tool_calls=calls)
        if not isinstance(text, str) or not text.strip():
            raise MagiModelError("provider_empty_response", retryable=True)
        if magi_text_has_unsafe_controls(text):
            raise MagiModelError("provider_unsafe_response", retryable=True)
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise MagiModelError("provider_invalid_unicode", retryable=True) from exc
        if len(text) > MAGI_MAX_RESPONSE_CHARACTERS or len(encoded) > MAGI_MAX_RESPONSE_BYTES:
            raise MagiModelError("response_too_large", retryable=True)
        return MagiModelResult(text=text, finish_reason="stop")
