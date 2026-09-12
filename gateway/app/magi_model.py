"""Provider-independent model boundary for native Magi chat.

Only complete final text crosses this module. Prompt/response content is never
logged, and provider tool/reasoning envelopes are never returned as prose.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit

import httpx

MAGI_MAX_RESPONSE_CHARACTERS = 200_000
MAGI_MAX_RESPONSE_BYTES = 256 * 1024
MAGI_DEFAULT_MAX_OUTPUT_TOKENS = 16_384


def magi_text_has_unsafe_controls(value: str) -> bool:
    return any(
        (code < 32 and code not in {9, 10, 13})
        or 127 <= code <= 159
        or 0xD800 <= code <= 0xDFFF
        for code in map(ord, value)
    )


@dataclass(frozen=True)
class MagiModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class MagiModelResult:
    """A complete provider result; ``text`` is preserved exactly as received."""

    text: str
    finish_reason: str = "stop"
    tool_calls: int = 0


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
    ) -> MagiModelResult:
        ...


class OpenAIMagiModel:
    """Concrete OpenAI Chat Completions provider for native Magi chat.

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
    ) -> MagiModelResult:
        if not self._api_key:
            raise MagiModelError("provider_not_configured", retryable=True)
        payload_messages = [{"role": "system", "content": system_context}]
        payload_messages.extend({"role": message.role, "content": message.content} for message in messages)
        # The stable idempotency key lets a provider that supports that standard
        # header collapse a transport retry without exposing the client id.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Idempotency-Key": request_id,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds, connect=min(10.0, self._timeout_seconds)),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "max_completion_tokens": self._max_output_tokens,
                        "stream": False,
                    },
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
            choice = payload["choices"][0]
            message = choice["message"]
            text = message.get("content")
            finish_reason = choice.get("finish_reason")
            tool_calls_raw = message.get("tool_calls") or []
            tool_calls = len(tool_calls_raw) if isinstance(tool_calls_raw, list) else 1
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise MagiModelError("provider_invalid_response", retryable=True) from exc
        if tool_calls:
            raise MagiModelError("provider_tool_call_unsupported", retryable=False, tool_calls=tool_calls)
        if finish_reason == "length":
            raise MagiModelError("response_limit_reached", retryable=True)
        if finish_reason != "stop":
            raise MagiModelError("provider_incomplete_response", retryable=True)
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
        return MagiModelResult(text=text, finish_reason="stop", tool_calls=0)
