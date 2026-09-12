"""Authenticated HTTP API for provider-native Magi chat.

The dependency graph of this router ends at auth, uploads, SQLite, and one model
provider. It deliberately does not import execution orchestration or terminal
infrastructure.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import Principal, require_any_scope, require_scope
from app.chat_features import validate_chat_feature_configuration
from app.contracts import NativeMagiMessageContract
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import (
    MAX_MAGI_HISTORY_MESSAGES,
    MagiChatConflict,
    MagiChatNotFound,
    MagiChatStore,
)
from app.magi_model import MagiModelMessage, MagiModelResult, OpenAIMagiModel
from app.uploads import associate_uploads, get_upload, validate_upload_metadata


def _configured_model() -> OpenAIMagiModel:
    provider = os.getenv("MAGISTRATE_MAGI_MODEL_PROVIDER", "openai").strip().lower()
    if provider != "openai":
        # Phase 1 intentionally supports one concrete provider. Unknown values
        # fail startup rather than silently selecting another inference path.
        raise RuntimeError("MAGISTRATE_MAGI_MODEL_PROVIDER must be openai")
    return OpenAIMagiModel()


def validate_magi_chat_configuration() -> None:
    model = _configured_model()
    if os.getenv("MAGISTRATE_ENV", "").strip().lower() == "production" and not model.configured:
        raise RuntimeError("Enabled native Magi chat requires server-side provider credentials.")


class _ConfiguredProvider:
    """Resolve secrets lazily so legacy rollback can boot without native config."""

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
    ) -> MagiModelResult:
        return await _configured_model().complete(
            messages, system_context=system_context, request_id=request_id,
        )


magi_chat_store = MagiChatStore()
magi_chat_service = MagiChatService(_ConfiguredProvider(), store=magi_chat_store)
router = APIRouter(prefix="/api/v1/magi", tags=["Magi native chat"])
_SAFE_CONVERSATION_ID = re.compile(r"^mgc_[A-Za-z0-9_-]{4,124}$")


def _require_native_chat() -> None:
    try:
        enabled, _ = validate_chat_feature_configuration()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Native Magi chat configuration is invalid.") from exc
    if not enabled:
        raise HTTPException(status_code=404, detail="Native Magi chat is disabled by compatibility configuration.")


def _conversation_id(value: str) -> str:
    if not _SAFE_CONVERSATION_ID.fullmatch(value):
        raise HTTPException(status_code=404, detail="Native Magi conversation not found.")
    return value


def _error_response(result: dict) -> dict:
    if result.get("status") == "failed":
        return {
            **result,
            "error": "Magi could not complete this response. The user message is saved and may be retried.",
            "retryable": True,
        }
    return result


@router.post("/messages")
async def post_magi_message(
    contract: NativeMagiMessageContract,
    principal: Principal = Depends(require_any_scope("command", "voice")),
):
    _require_native_chat()
    attachments = []
    for attachment in contract.attachments:
        stored = get_upload(principal.user_id, attachment.upload_id)
        if not stored:
            raise HTTPException(status_code=404, detail="One or more attached files are unavailable.")
        try:
            validate_upload_metadata(
                stored, attachment.filename, attachment.media_type, attachment.size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        attachments.append(stored)
    if attachments:
        try:
            associate_uploads(
                principal.user_id,
                contract.client_message_id,
                [attachment["upload_id"] for attachment in attachments],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = await magi_chat_service.submit(
            principal.user_id,
            contract.client_message_id,
            contract.content,
            conversation_id=contract.conversation_id,
            source=contract.source,
            attachments=attachments,
            retry_failed=contract.retry_failed,
        )
    except MagiChatNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MagiChatConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _error_response(result)


@router.get("/conversations/current")
async def get_current_magi_conversation(
    before: Optional[int] = Query(None, ge=1),
    limit: int = Query(MAX_MAGI_HISTORY_MESSAGES, ge=1, le=MAX_MAGI_HISTORY_MESSAGES),
    principal: Principal = Depends(require_scope("read")),
):
    _require_native_chat()
    try:
        return await magi_chat_service.current_conversation(
            principal.user_id, before=before, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}")
async def get_magi_conversation(
    conversation_id: str,
    before: Optional[int] = Query(None, ge=1),
    limit: int = Query(MAX_MAGI_HISTORY_MESSAGES, ge=1, le=MAX_MAGI_HISTORY_MESSAGES),
    principal: Principal = Depends(require_scope("read")),
):
    _require_native_chat()
    try:
        return await magi_chat_service.conversation(
            principal.user_id,
            _conversation_id(conversation_id),
            before=before,
            limit=limit,
        )
    except MagiChatNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}/replay")
async def replay_magi_conversation(
    conversation_id: str,
    after: int = Query(0, ge=0, le=9_007_199_254_740_991),
    limit: int = Query(MAX_MAGI_HISTORY_MESSAGES, ge=1, le=MAX_MAGI_HISTORY_MESSAGES),
    principal: Principal = Depends(require_scope("read")),
):
    _require_native_chat()
    try:
        return await magi_chat_service.replay(
            principal.user_id,
            _conversation_id(conversation_id),
            after=after,
            limit=limit,
        )
    except MagiChatNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MagiChatConflict, ValueError) as exc:
        raise HTTPException(status_code=409 if isinstance(exc, MagiChatConflict) else 422, detail=str(exc)) from exc


@router.post("/messages/{client_message_id}/cancel")
async def cancel_magi_message(
    client_message_id: str,
    principal: Principal = Depends(require_any_scope("command", "voice")),
):
    _require_native_chat()
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$", client_message_id):
        raise HTTPException(status_code=404, detail="Native Magi submission not found.")
    try:
        return await magi_chat_service.cancel(principal.user_id, client_message_id)
    except MagiChatNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/diagnostics")
async def get_magi_chat_diagnostics(
    principal: Principal = Depends(require_scope("read")),
):
    _require_native_chat()
    return await magi_chat_service.diagnostics(principal.user_id)
