"""Replaceable native-model provider adapters.

Adapters translate provider wire formats into :mod:`app.magi_model`'s closed
message/tool/result contract. They never return reasoning blocks or provider
error bodies, and credentials remain in headers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit

import httpx

from app.magi_model import (
    MAGI_DEFAULT_MAX_OUTPUT_TOKENS,
    MAGI_MAX_RESPONSE_BYTES,
    MAGI_MAX_RESPONSE_CHARACTERS,
    MAGI_MAX_TOOL_CALLS,
    MagiModelAttachment,
    MagiModelError,
    MagiModelMessage,
    MagiModelResult,
    MagiModelToolCall,
    MagiModelUsage,
    MagiToolDefinition,
    _provider_tools,
    _validated_tool_call,
    _validated_utf8,
    magi_text_has_unsafe_controls,
)


def _encoded_attachment(attachment: MagiModelAttachment) -> str:
    if (
        not isinstance(attachment, MagiModelAttachment)
        or not attachment.filename or len(attachment.filename) > 160
        or not isinstance(attachment.content, bytes)
        or not 0 < len(attachment.content) <= 25 * 1024 * 1024
        or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", attachment.media_type)
    ):
        raise MagiModelError("provider_invalid_attachment", retryable=False)
    return base64.b64encode(attachment.content).decode("ascii")


def _safe_url(value: str, variable: str) -> str:
    result = value.rstrip("/")
    parsed = urlsplit(result)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
    ):
        raise RuntimeError(f"{variable} must be a credential-free HTTPS URL")
    return result


def _safe_model(value: str, variable: str) -> str:
    result = value.strip()
    if (
        not result or len(result) > 128
        or any(not (character.isalnum() or character in "._:/-") for character in result)
    ):
        raise RuntimeError(f"{variable} is invalid")
    return result


def _safe_limits(max_output_tokens: int, timeout_seconds: float) -> None:
    if not 1 <= max_output_tokens <= 65_536:
        raise RuntimeError("model max output tokens must be between 1 and 65536")
    if not 1 <= timeout_seconds <= 300:
        raise RuntimeError("model timeout seconds must be between 1 and 300")


def _safe_text(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise MagiModelError("provider_empty_response")
    if magi_text_has_unsafe_controls(text):
        raise MagiModelError("provider_unsafe_response")
    encoded = _validated_utf8(
        text, maximum_bytes=MAGI_MAX_RESPONSE_BYTES, code="response_too_large",
    )
    if len(text) > MAGI_MAX_RESPONSE_CHARACTERS or len(encoded) > MAGI_MAX_RESPONSE_BYTES:
        raise MagiModelError("response_too_large")
    return text


def _usage(input_tokens: Any, output_tokens: Any, cached_tokens: Any = 0) -> MagiModelUsage | None:
    values = (input_tokens, output_tokens, cached_tokens)
    if not all(type(value) is int and value >= 0 for value in values):
        return None
    return MagiModelUsage(*values)


def _http_error(response: httpx.Response) -> MagiModelError:
    retryable = response.status_code == 429 or response.status_code >= 500
    return MagiModelError("provider_rejected_request", retryable=retryable)


class AnthropicMagiModel:
    """Anthropic Messages adapter behind the canonical Magi boundary."""

    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-3-5-haiku-latest",
        base_url: str = "https://api.anthropic.com/v1",
        max_output_tokens: int = MAGI_DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")
        self.model = _safe_model(model, "Anthropic model")
        self._base_url = _safe_url(base_url, "Anthropic provider URL")
        self._max_output_tokens = int(max_output_tokens)
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport
        _safe_limits(self._max_output_tokens, self._timeout_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _messages(messages: Sequence[MagiModelMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        calls: dict[str, str] = {}
        for message in messages:
            if not isinstance(message, MagiModelMessage):
                raise MagiModelError("provider_invalid_request", retryable=False)
            if message.role == "tool":
                if not message.tool_call_id or message.tool_call_id not in calls or not isinstance(message.content, str):
                    raise MagiModelError("provider_invalid_request", retryable=False)
                content: Any = [{
                    "type": "tool_result", "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }]
                role = "user"
            elif message.role == "assistant" and message.tool_calls:
                content = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    checked = _validated_tool_call(call)
                    try:
                        arguments = json.loads(checked["function"]["arguments"])
                    except json.JSONDecodeError as exc:
                        raise MagiModelError("provider_invalid_request", retryable=False) from exc
                    content.append({
                        "type": "tool_use", "id": call.id, "name": checked["function"]["name"],
                        "input": arguments,
                    })
                    calls[call.id] = checked["function"]["name"]
                role = "assistant"
            elif message.role in {"user", "assistant"} and isinstance(message.content, str):
                _validated_utf8(
                    message.content, maximum_bytes=MAGI_MAX_RESPONSE_BYTES,
                    code="provider_invalid_request",
                )
                if message.attachments:
                    if message.role != "user" or len(message.attachments) > 10:
                        raise MagiModelError("provider_invalid_attachment", retryable=False)
                    content = [{"type": "text", "text": message.content}]
                    for attachment in message.attachments:
                        encoded = _encoded_attachment(attachment)
                        if attachment.media_type.startswith("image/") and attachment.media_type != "image/bmp":
                            content.append({
                                "type": "image",
                                "source": {"type": "base64", "media_type": attachment.media_type, "data": encoded},
                            })
                        elif attachment.media_type == "application/pdf":
                            content.append({
                                "type": "document",
                                "source": {"type": "base64", "media_type": attachment.media_type, "data": encoded},
                                "title": attachment.filename,
                            })
                        elif attachment.media_type.startswith("text/") or attachment.media_type in {
                            "application/json", "application/xml", "application/javascript",
                        }:
                            try:
                                text = attachment.content.decode("utf-8")
                            except UnicodeDecodeError as exc:
                                raise MagiModelError("provider_invalid_attachment", retryable=False) from exc
                            content.append({
                                "type": "text",
                                "text": f"<attachment name={json.dumps(attachment.filename)}>{text}</attachment>",
                            })
                        else:
                            raise MagiModelError("provider_invalid_attachment", retryable=False)
                else:
                    content = message.content
                role = message.role
            else:
                raise MagiModelError("provider_invalid_request", retryable=False)
            # Anthropic requires alternating roles. Tool results naturally join
            # the preceding user block when several host tools ever exist.
            if result and result[-1]["role"] == role:
                previous = result[-1]["content"]
                previous_blocks = previous if isinstance(previous, list) else [{"type": "text", "text": previous}]
                blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
                result[-1]["content"] = [*previous_blocks, *blocks]
            else:
                result.append({"role": role, "content": content})
        return result

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        if not self.configured:
            raise MagiModelError("provider_not_configured")
        _validated_utf8(system_context, maximum_bytes=MAGI_MAX_RESPONSE_BYTES, code="provider_invalid_request")
        tool_payloads, provider_names = _provider_tools(tools)
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system_context,
            "messages": self._messages(messages),
            "max_tokens": self._max_output_tokens,
        }
        if tool_payloads:
            payload["tools"] = [{
                "name": item["name"], "description": item["description"],
                "input_schema": item["parameters"],
            } for item in tool_payloads]
            payload["tool_choice"] = {"type": "any", "disable_parallel_tool_use": True}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds, connect=min(10, self._timeout_seconds)),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "Idempotency-Key": request_id,
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise MagiModelError("provider_timeout", billing_uncertain=True) from exc
        except httpx.ConnectError as exc:
            raise MagiModelError("provider_unavailable") from exc
        except httpx.HTTPError as exc:
            raise MagiModelError("provider_unavailable", billing_uncertain=True) from exc
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            body = response.json()
            content = body["content"]
            stop_reason = body["stop_reason"]
            if not isinstance(content, list) or not isinstance(stop_reason, str):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise MagiModelError("provider_invalid_response") from exc
        texts: list[str] = []
        calls: list[MagiModelToolCall] = []
        for block in content:
            if not isinstance(block, Mapping):
                raise MagiModelError("provider_invalid_response")
            if block.get("type") == "thinking":
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
                continue
            if block.get("type") == "tool_use":
                if not all(isinstance(block.get(key), str) for key in ("id", "name")):
                    raise MagiModelError("provider_invalid_tool_call", retryable=False, tool_calls=1)
                name = provider_names.get(block["name"])
                if name is None:
                    raise MagiModelError("provider_unknown_tool_call", retryable=False, tool_calls=1)
                try:
                    arguments = json.dumps(block.get("input"), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise MagiModelError("provider_invalid_tool_call", retryable=False, tool_calls=1) from exc
                call = MagiModelToolCall(block["id"], name, arguments)
                _validated_tool_call(call)
                calls.append(call)
                if len(calls) > MAGI_MAX_TOOL_CALLS:
                    raise MagiModelError(
                        "provider_invalid_tool_call", retryable=False, tool_calls=len(calls),
                    )
                continue
            raise MagiModelError("provider_invalid_response")
        raw_usage = body.get("usage") if isinstance(body, Mapping) else None
        result_usage = _usage(
            raw_usage.get("input_tokens") if isinstance(raw_usage, Mapping) else None,
            raw_usage.get("output_tokens") if isinstance(raw_usage, Mapping) else None,
            raw_usage.get("cache_read_input_tokens", 0) if isinstance(raw_usage, Mapping) else 0,
        )
        text = "".join(texts) or None
        if calls:
            if stop_reason != "tool_use" or not tool_payloads:
                raise MagiModelError("provider_tool_call_unsupported", retryable=False, tool_calls=len(calls))
            return MagiModelResult(text, "tool_calls", tuple(calls), result_usage)
        if stop_reason == "max_tokens":
            raise MagiModelError("response_limit_reached")
        if stop_reason not in {"end_turn", "stop_sequence"}:
            raise MagiModelError("provider_incomplete_response")
        return MagiModelResult(_safe_text(text), usage=result_usage)


class GoogleMagiModel:
    """Google Generative Language ``generateContent`` adapter."""

    provider_id = "google"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        max_output_tokens: int = MAGI_DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GOOGLE_API_KEY", "")
        self.model = _safe_model(model, "Google model")
        self._base_url = _safe_url(base_url, "Google provider URL")
        self._max_output_tokens = int(max_output_tokens)
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport
        _safe_limits(self._max_output_tokens, self._timeout_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _contents(messages: Sequence[MagiModelMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        calls: dict[str, str] = {}
        for message in messages:
            parts: list[dict[str, Any]] = []
            if message.role == "tool":
                name = calls.get(message.tool_call_id or "")
                if name is None or not isinstance(message.content, str):
                    raise MagiModelError("provider_invalid_request", retryable=False)
                try:
                    response = json.loads(message.content)
                except json.JSONDecodeError:
                    response = {"result": message.content}
                parts.append({"functionResponse": {"name": name, "response": response}})
                role = "user"
            elif message.role in {"user", "assistant"}:
                role = "model" if message.role == "assistant" else "user"
                if message.content:
                    _validated_utf8(
                        message.content, maximum_bytes=MAGI_MAX_RESPONSE_BYTES,
                        code="provider_invalid_request",
                    )
                    parts.append({"text": message.content})
                if message.attachments:
                    if message.role != "user" or len(message.attachments) > 10:
                        raise MagiModelError("provider_invalid_attachment", retryable=False)
                    for attachment in message.attachments:
                        parts.append({"inlineData": {
                            "mimeType": attachment.media_type,
                            "data": _encoded_attachment(attachment),
                        }})
                for call in message.tool_calls:
                    checked = _validated_tool_call(call)
                    try:
                        arguments = json.loads(call.arguments_json)
                    except json.JSONDecodeError as exc:
                        raise MagiModelError("provider_invalid_request", retryable=False) from exc
                    name = checked["function"]["name"]
                    parts.append({"functionCall": {"name": name, "args": arguments}})
                    calls[call.id] = name
                if not parts:
                    raise MagiModelError("provider_invalid_request", retryable=False)
            else:
                raise MagiModelError("provider_invalid_request", retryable=False)
            result.append({"role": role, "parts": parts})
        return result

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        if not self.configured:
            raise MagiModelError("provider_not_configured")
        _validated_utf8(system_context, maximum_bytes=MAGI_MAX_RESPONSE_BYTES, code="provider_invalid_request")
        tool_payloads, provider_names = _provider_tools(tools)
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_context}]},
            "contents": self._contents(messages),
            "generationConfig": {"maxOutputTokens": self._max_output_tokens},
        }
        if tool_payloads:
            payload["tools"] = [{"functionDeclarations": [{
                "name": item["name"], "description": item["description"],
                "parameters": item["parameters"],
            } for item in tool_payloads]}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds, connect=min(10, self._timeout_seconds)),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/models/{quote(self.model, safe='._-')}:generateContent",
                    headers={"x-goog-api-key": self._api_key, "X-Request-Id": request_id},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise MagiModelError("provider_timeout", billing_uncertain=True) from exc
        except httpx.ConnectError as exc:
            raise MagiModelError("provider_unavailable") from exc
        except httpx.HTTPError as exc:
            raise MagiModelError("provider_unavailable", billing_uncertain=True) from exc
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            body = response.json()
            candidates = body["candidates"]
            candidate = candidates[0]
            finish = candidate["finishReason"]
            parts = candidate["content"]["parts"]
            if len(candidates) != 1 or not isinstance(parts, list) or not isinstance(finish, str):
                raise TypeError
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise MagiModelError("provider_invalid_response") from exc
        texts: list[str] = []
        calls: list[MagiModelToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, Mapping):
                raise MagiModelError("provider_invalid_response")
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
                continue
            raw_call = part.get("functionCall")
            if raw_call is not None:
                if not isinstance(raw_call, Mapping) or not isinstance(raw_call.get("name"), str):
                    raise MagiModelError("provider_invalid_tool_call", retryable=False, tool_calls=1)
                name = provider_names.get(raw_call["name"])
                if name is None:
                    raise MagiModelError("provider_unknown_tool_call", retryable=False, tool_calls=1)
                try:
                    arguments = json.dumps(raw_call.get("args", {}), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise MagiModelError("provider_invalid_tool_call", retryable=False, tool_calls=1) from exc
                call_id = "gcall_" + hashlib.sha256(f"{request_id}\0{index}\0{name}".encode()).hexdigest()[:24]
                call = MagiModelToolCall(call_id, name, arguments)
                _validated_tool_call(call)
                calls.append(call)
                if len(calls) > MAGI_MAX_TOOL_CALLS:
                    raise MagiModelError(
                        "provider_invalid_tool_call", retryable=False, tool_calls=len(calls),
                    )
                continue
            # Thought signatures and any future private blocks are not prose.
            if "thoughtSignature" in part:
                continue
            raise MagiModelError("provider_invalid_response")
        raw_usage = body.get("usageMetadata") if isinstance(body, Mapping) else None
        result_usage = _usage(
            raw_usage.get("promptTokenCount") if isinstance(raw_usage, Mapping) else None,
            raw_usage.get("candidatesTokenCount") if isinstance(raw_usage, Mapping) else None,
            raw_usage.get("cachedContentTokenCount", 0) if isinstance(raw_usage, Mapping) else 0,
        )
        text = "".join(texts) or None
        if calls:
            if not tool_payloads:
                raise MagiModelError("provider_tool_call_unsupported", retryable=False, tool_calls=len(calls))
            return MagiModelResult(text, "tool_calls", tuple(calls), result_usage)
        if finish == "MAX_TOKENS":
            raise MagiModelError("response_limit_reached")
        if finish != "STOP":
            raise MagiModelError("provider_incomplete_response")
        return MagiModelResult(_safe_text(text), usage=result_usage)
