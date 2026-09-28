"""Authenticated producer API for structured Firstmate execution events."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from app.auth import Principal, require_any_scope, require_scope
from app.billing import BillingError
from app.firstmate_execution import (
    FirstmateCompletionWakeContract,
    FirstmateExecutionConflict,
    FirstmateExecutionEventContract,
    FirstmateExecutionNotFound,
    FirstmateExecutionService,
)
from app.magi_chat_api import magi_chat_service
from app.telemetry import operation_span

router = APIRouter(prefix="/api/v1/firstmate/execution-events", tags=["Firstmate execution events"])
firstmate_execution_service = FirstmateExecutionService(magi_chat_service)
_EVENT_PATH = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _execution_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FirstmateExecutionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FirstmateExecutionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BillingError):
        return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
    return HTTPException(status_code=422, detail=str(exc))


@router.post("")
async def post_firstmate_execution_event(
    event: FirstmateExecutionEventContract,
    principal: Principal = Depends(require_any_scope("response", "command")),
):
    """Persist one owner-scoped event and wake verified completion if present."""
    from app.hosted_execution import hosted_execution_enabled
    if hosted_execution_enabled():
        raise HTTPException(
            status_code=403,
            detail="Hosted execution events require objective-bound workload authentication.",
        )
    try:
        with operation_span('execution_ingress', objective_id=event.objective_id):
            return await firstmate_execution_service.ingest(principal.user_id, event)
    except (FirstmateExecutionNotFound, FirstmateExecutionConflict, BillingError, ValueError) as exc:
        raise _execution_error(exc) from exc


@router.get("/{event_id}")
async def get_firstmate_execution_event(
    event_id: str = _EVENT_PATH,
    principal: Principal = Depends(require_scope("read")),
):
    try:
        return firstmate_execution_service.store.inspect(principal.user_id, event_id)
    except (FirstmateExecutionNotFound, ValueError) as exc:
        raise _execution_error(exc) from exc


@router.post("/{event_id}/wake")
async def wake_firstmate_completion_event(
    contract: FirstmateCompletionWakeContract,
    event_id: str = _EVENT_PATH,
    principal: Principal = Depends(require_any_scope("response", "command")),
):
    """Explicitly retry a failed completion report without replaying its event."""
    try:
        return await firstmate_execution_service.wake(
            principal.user_id,
            event_id,
            retry_failed=contract.retry_failed,
        )
    except (FirstmateExecutionNotFound, FirstmateExecutionConflict, BillingError, ValueError) as exc:
        raise _execution_error(exc) from exc
