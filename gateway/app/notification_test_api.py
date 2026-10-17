"""Owner-triggered push test. Mount router in Gateway composition (no startup send)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Principal, require_scope
from app.notifications import send_owner_test_notification

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


class NotificationTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")


@router.post("/test")
async def test_notification(
    request: NotificationTestRequest,
    principal: Principal = Depends(require_scope("notifications")),
):
    return await send_owner_test_notification(principal.user_id, request.request_id)
