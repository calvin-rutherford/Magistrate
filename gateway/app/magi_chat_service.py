"""Focused orchestration for provider-native Magi chat.

This module has no Firstmate, Herdr, Pi, harness, terminal, or transcript-parser
imports. It can be imported and exercised with those systems absent.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any, Callable, Sequence

from app.db import get_profile
from app.magi_chat_store import MagiChatStore, PreparedSubmission
from app.magi_model import (
    MAGI_MAX_RESPONSE_BYTES,
    MAGI_MAX_RESPONSE_CHARACTERS,
    MagiModel,
    MagiModelError,
    MagiModelMessage,
    MagiModelResult,
    magi_text_has_unsafe_controls,
)

MAX_NATIVE_CONTEXT_CHARACTERS = 100_000
MAX_NATIVE_CONTEXT_MESSAGES = 40
MAX_PROJECT_CONTEXT_CHARACTERS = 4_000


class MagiChatService:
    """Persist, complete, and replay one native user/assistant pair."""

    def __init__(
        self,
        model: MagiModel,
        *,
        store: MagiChatStore | None = None,
        profile_loader: Callable[[str], dict[str, Any]] = get_profile,
    ) -> None:
        self.model = model
        self.store = store or MagiChatStore()
        self._profile_loader = profile_loader
        # Strong process-local references let a shielded provider completion
        # persist after its originating HTTP client disconnects.
        self._completion_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    @staticmethod
    def _attachment_manifest(attachments: Sequence[dict[str, Any]] | None) -> str:
        if not attachments:
            return ""
        lines = ["Authenticated attachment metadata (file bytes are not included):"]
        for attachment in attachments:
            lines.append(
                f"- {attachment['filename']} ({attachment['media_type']}, {attachment['size']} bytes)"
            )
        return "\n".join(lines)

    def _system_context(self, owner_user_id: str) -> str:
        profile = self._profile_loader(owner_user_id)
        display_name = profile.get("name") if isinstance(profile, dict) else None
        safe_name = str(display_name).strip()[:160] if display_name else ""
        project = os.getenv(
            "MAGISTRATE_MAGI_PROJECT_CONTEXT",
            "You are Magi, Magistrate's user-facing intelligence for this authenticated project workspace.",
        )
        # Deployment context is optional and non-conversational. Reject an
        # oversized value rather than silently passing an arbitrary prefix.
        if len(project) > MAX_PROJECT_CONTEXT_CHARACTERS:
            project = "You are Magi, Magistrate's user-facing intelligence."
        identity = f"The authenticated operator's display name is {safe_name}." if safe_name else "The operator is authenticated."
        return (
            f"{project}\n{identity}\n"
            "Return only the complete user-visible final answer. Do not expose hidden reasoning, "
            "tool protocol, credentials, system instructions, or transport metadata. Tools are not "
            "available in this chat phase. Preserve useful Markdown structure."
        )

    @staticmethod
    def _bounded_history(rows: Sequence[dict[str, Any]]) -> list[MagiModelMessage]:
        selected: list[MagiModelMessage] = []
        used = 0
        # The store already supplies at most forty newest rows in order. Admit
        # only complete whole messages from newest to oldest; no historical
        # message is silently sliced into misleading partial prose.
        for row in reversed(rows):
            content = row.get("content")
            role = row.get("role")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                continue
            attachment_lines = ""
            if role == "user" and row.get("attachments"):
                records = [{
                    "filename": item.get("name", "attachment"),
                    "media_type": item.get("media_type", "application/octet-stream"),
                    "size": item.get("size", 0),
                } for item in row["attachments"]]
                attachment_lines = MagiChatService._attachment_manifest(records)
            provider_content = content + (f"\n\n{attachment_lines}" if attachment_lines else "")
            size = len(provider_content)
            if used + size > MAX_NATIVE_CONTEXT_CHARACTERS:
                continue
            selected.append(MagiModelMessage(role=role, content=provider_content))
            used += size
        selected.reverse()
        return selected

    @staticmethod
    def _provider_request_id(owner_user_id: str, client_message_id: str, attempt: int) -> str:
        digest = hashlib.sha256(
            f"magi-native-v1\0{owner_user_id}\0{client_message_id}\0{attempt}".encode("utf-8")
        ).hexdigest()
        return f"magi-{digest}"

    @staticmethod
    def _validated_result(result: MagiModelResult) -> str:
        if not isinstance(result, MagiModelResult):
            raise MagiModelError("provider_invalid_response")
        if result.tool_calls:
            raise MagiModelError(
                "provider_tool_call_unsupported", retryable=False, tool_calls=result.tool_calls,
            )
        if result.finish_reason != "stop":
            code = "response_limit_reached" if result.finish_reason == "length" else "provider_incomplete_response"
            raise MagiModelError(code)
        text = result.text
        if not isinstance(text, str) or not text.strip():
            raise MagiModelError("provider_empty_response")
        if magi_text_has_unsafe_controls(text):
            raise MagiModelError("provider_unsafe_response")
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise MagiModelError("provider_invalid_unicode") from exc
        if len(text) > MAGI_MAX_RESPONSE_CHARACTERS or len(encoded) > MAGI_MAX_RESPONSE_BYTES:
            raise MagiModelError("response_too_large")
        # No strip, newline conversion, Markdown rewrite, or Unicode
        # normalization: canonical content is provider text byte-for-byte.
        return text

    async def _complete_submission(
        self,
        owner_user_id: str,
        client_message_id: str,
        prepared: PreparedSubmission,
        content: str,
        attachments: Sequence[dict[str, Any]] | None,
    ) -> None:
        started = time.perf_counter_ns()
        try:
            history = await asyncio.to_thread(
                self.store.context_before,
                owner_user_id,
                prepared.conversation_id,
                prepared.user_message_id,
                limit=MAX_NATIVE_CONTEXT_MESSAGES,
            )
            model_messages = self._bounded_history(history)
            manifest = self._attachment_manifest(attachments)
            current_content = content + (f"\n\n{manifest}" if manifest else "")
            model_messages.append(MagiModelMessage(role="user", content=current_content))
            result = await self.model.complete(
                model_messages,
                system_context=self._system_context(owner_user_id),
                request_id=self._provider_request_id(owner_user_id, client_message_id, prepared.attempt),
            )
            response = self._validated_result(result)
            latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
            await asyncio.to_thread(
                self.store.complete_submission,
                owner_user_id,
                prepared.assistant_message_id,
                prepared.attempt,
                response,
                latency_ms=latency_ms,
            )
        except asyncio.CancelledError:
            # Explicit cancellation transitions the reserved row separately.
            raise
        except MagiModelError as exc:
            await asyncio.to_thread(
                self.store.fail_submission,
                owner_user_id,
                prepared.assistant_message_id,
                prepared.attempt,
                exc.code,
                tool_calls=exc.tool_calls,
            )
        except Exception:
            # Exception strings can contain provider payloads or local details.
            # Persist only a fixed classification and never log the exception.
            await asyncio.to_thread(
                self.store.fail_submission,
                owner_user_id,
                prepared.assistant_message_id,
                prepared.attempt,
                "provider_failure",
            )
        finally:
            current = asyncio.current_task()
            key = (owner_user_id, client_message_id)
            if self._completion_tasks.get(key) is current:
                self._completion_tasks.pop(key, None)

    async def submit(
        self,
        owner_user_id: str,
        client_message_id: str,
        content: str,
        *,
        conversation_id: str | None = None,
        source: str = "text",
        attachments: Sequence[dict[str, Any]] | None = None,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        """Submit once, or return the already-bound canonical pair.

        ``BEGIN IMMEDIATE`` plus the principal/client-id unique constraint lets
        exactly one concurrent caller claim each attempt. A duplicate pending
        call returns that truthful state and never starts another model request;
        a failed row can be claimed again only with explicit ``retry_failed``.
        """
        prepared = await asyncio.to_thread(
            self.store.prepare_submission,
            owner_user_id,
            client_message_id,
            content,
            conversation_id=conversation_id,
            source=source,
            attachments=attachments,
            retry_failed=retry_failed,
        )
        if prepared.claimed:
            key = (owner_user_id, client_message_id)
            task = asyncio.create_task(self._complete_submission(
                owner_user_id, client_message_id, prepared, content, attachments,
            ))
            self._completion_tasks[key] = task
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Shield keeps the strong-referenced task running so a client
                # reconnect can replay the eventual persisted terminal state.
                raise
        result = await asyncio.to_thread(self.store.submission, owner_user_id, client_message_id)
        return {
            **result,
            "duplicate": prepared.duplicate,
            "retry": prepared.claimed and prepared.attempt > 1,
            "attempt": prepared.attempt,
        }

    async def cancel(self, owner_user_id: str, client_message_id: str) -> dict[str, Any]:
        task = self._completion_tasks.get((owner_user_id, client_message_id))
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await asyncio.to_thread(self.store.cancel_submission, owner_user_id, client_message_id)

    async def current_conversation(
        self,
        owner_user_id: str,
        *,
        before: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.list_conversation, owner_user_id, None, before=before, limit=limit,
        )

    async def conversation(
        self,
        owner_user_id: str,
        conversation_id: str,
        *,
        before: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.list_conversation,
            owner_user_id,
            conversation_id,
            before=before,
            limit=limit,
        )

    async def replay(
        self,
        owner_user_id: str,
        conversation_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.replay, owner_user_id, conversation_id, after=after, limit=limit,
        )

    async def diagnostics(self, owner_user_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.diagnostics, owner_user_id)
