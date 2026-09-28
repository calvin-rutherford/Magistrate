"""Authenticated HTTP API for provider-native Magi chat.

Canonical conversation remains isolated from terminals and harnesses. The only
orchestration edge is an injected, closed Firstmate objective tool whose owner
and chat identity come from this authenticated route rather than model JSON.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import Principal, require_any_scope, require_scope
from app.contracts import NativeMagiMessageContract
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import (
    MAX_MAGI_HISTORY_MESSAGES,
    MagiChatConflict,
    MagiChatNotFound,
    MagiChatStore,
)
from app.magi_firstmate_tools import FirstmateObjectiveTools
from app.magi_model import (
    MagiModelMessage,
    MagiModelResult,
    MagiToolDefinition,
)
from app.magi_routing import (
    ModelRouteContext,
    ModelRouteStore,
    RoutedMagiModel,
    load_routing_catalog,
)
from app.uploads import associate_uploads, get_upload, validate_upload_metadata


model_route_store = ModelRouteStore()


def _configured_model() -> RoutedMagiModel:
    legacy_provider = os.getenv("MAGISTRATE_MAGI_MODEL_PROVIDER", "").strip().lower()
    if legacy_provider and legacy_provider != "routed":
        raise RuntimeError(
            "MAGISTRATE_MAGI_MODEL_PROVIDER is retired; configure the routed provider catalog"
        )
    return RoutedMagiModel(load_routing_catalog(), store=model_route_store)


def validate_magi_chat_configuration() -> None:
    model = _configured_model()
    if os.getenv("MAGISTRATE_ENV", "").strip().lower() == "production" and not model.configured:
        raise RuntimeError("Enabled native Magi chat requires server-side provider credentials.")


def magi_chat_readiness() -> dict[str, object]:
    """Report static provider configuration without issuing a model request."""
    try:
        model = _configured_model()
    except RuntimeError:
        return {
            "status": "invalid", "enabled": True,
            "provider": None, "live_probe_performed": False,
        }
    configured_providers = list(model.configured_provider_ids)
    return {
        "status": "configured" if model.configured else "unconfigured",
        "enabled": True,
        "provider": "routed",
        "configured_providers": configured_providers,
        "live_probe_performed": False,
    }


class _ConfiguredProvider:
    """Resolve routing policy and provider secrets at the request boundary."""

    async def complete_routed(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        route_context: ModelRouteContext,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        return await _configured_model().complete_routed(
            messages, system_context=system_context, request_id=request_id,
            route_context=route_context, tools=tools,
        )

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        return await _configured_model().complete(
            messages, system_context=system_context, request_id=request_id, tools=tools,
        )


magi_chat_store = MagiChatStore()
magi_objective_tools = FirstmateObjectiveTools()
magi_chat_service = MagiChatService(
    _ConfiguredProvider(), store=magi_chat_store, tool_executor=magi_objective_tools,
)
router = APIRouter(prefix="/api/v1/magi", tags=["Magi native chat"])
_SAFE_CONVERSATION_ID = re.compile(r"^mgc_[A-Za-z0-9_-]{4,124}$")


def _conversation_id(value: str) -> str:
    if not _SAFE_CONVERSATION_ID.fullmatch(value):
        raise HTTPException(status_code=404, detail="Native Magi conversation not found.")
    return value


def _error_response(result: dict) -> dict:
    if result.get("status") == "failed":
        code = result.get("error_code")
        if code == "route_fallback_confirmation_required":
            message = (
                "The available fallback may materially increase cost. Review the request and retry "
                "with explicit confirmation to authorize that fallback."
            )
        elif code == "model_budget_exhausted":
            message = "The configured model budget is exhausted. No paid fallback was attempted."
        elif code == "no_reliably_capable_model":
            message = "No configured, available model reliably satisfies this request."
        else:
            message = "Magi could not complete this response. The user message is saved and may be retried."
        return {
            **result,
            "error": message,
            "retryable": code != "model_budget_exhausted",
        }
    return result


@router.post("/messages")
async def post_magi_message(
    contract: NativeMagiMessageContract,
    principal: Principal = Depends(require_any_scope("command", "voice")),
):
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
            # Conversation is available to voice-only principals, but a model
            # can receive an execution tool only with the existing command
            # authority. Tool JSON can never grant that scope to itself.
            allow_tools=principal.has("command"),
            explicit_confirmation=contract.explicit_confirmation,
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
    return await magi_chat_service.diagnostics(principal.user_id)


@router.get("/model-routes")
async def get_magi_model_routes(
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = Depends(require_scope("read")),
):
    """Return principal-scoped, content-free route/cost/failover evidence."""
    paid = await asyncio.to_thread(
        model_route_store.summary, principal.user_id, limit=limit,
    )
    closures = await asyncio.to_thread(
        magi_chat_store.route_closures, principal.user_id, limit=limit,
    )
    return {**paid, "turn_closures": closures}
