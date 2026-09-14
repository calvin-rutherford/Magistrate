"""Authenticated push seam for persisted Firstmate decision projections."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import Principal, require_any_scope
from app.firstmate_decisions import (
    FirstmateDecisionError,
    FirstmateDecisionEventBatch,
    firstmate_decisions,
)

router = APIRouter(prefix="/api/v1/firstmate/decision-events", tags=["Firstmate decision events"])


@router.post("")
async def post_firstmate_decision_events(
    batch: FirstmateDecisionEventBatch,
    principal: Principal = Depends(require_any_scope("response", "command")),
):
    """Persist a complete owner-scoped projection; never inspect live runtime."""
    try:
        decisions = await firstmate_decisions.ingest_events(principal.user_id, batch)
    except FirstmateDecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "schema_version": "firstmate.decision-events-result.v1",
        "status": "accepted",
        "pending_count": len(decisions),
        "observed_at": batch.observed_at,
    }
