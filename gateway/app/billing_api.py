"""Authenticated billing surfaces and unauthenticated signed Stripe ingress."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.auth import Principal, require_scope
from app.billing import (
    BillingError, BillingService, StripeWebhookProcessor, accept_webhook,
    billing_available, billing_status, create_checkout as create_legacy_checkout,
    create_portal as create_legacy_portal, load_catalog, stripe_configured,
)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
billing_service = BillingService()
webhook_processor = StripeWebhookProcessor()


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CheckoutRequest(_StrictRequest):
    catalog_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    return_url: str = Field(min_length=1, max_length=2048)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class PortalRequest(_StrictRequest):
    return_url: str = Field(min_length=1, max_length=2048)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


def _error(exc: BillingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


@router.get("/catalog")
async def billing_catalog(principal: Principal = Depends(require_scope("read"))):
    del principal
    try:
        return load_catalog().public()
    except BillingError as exc:
        raise _error(exc) from exc


@router.get("/status")
async def legacy_billing_status(principal: Principal = Depends(require_scope("account"))):
    return billing_status(principal.user_id)


@router.get("/account")
async def billing_account(principal: Principal = Depends(require_scope("account"))):
    try:
        return billing_service.ledger.summary(principal.user_id)
    except BillingError as exc:
        raise _error(exc) from exc


@router.post("/checkout")
async def create_checkout(
    contract: CheckoutRequest | None = None,
    principal: Principal = Depends(require_scope("account")),
):
    try:
        if contract is None or (billing_available() and not stripe_configured()):
            profile = db.get_profile(principal.user_id)
            email = profile.get("email") if isinstance(profile, dict) else None
            legacy = await create_legacy_checkout(
                principal.user_id, email if isinstance(email, str) else None,
            )
            if contract is None:
                return legacy
            return {
                "checkout_url": legacy["url"],
                "session_id": legacy["checkout_session_id"],
            }
        return await billing_service.checkout(principal.user_id, contract.catalog_id, contract.return_url, contract.idempotency_key)
    except BillingError as exc:
        raise _error(exc) from exc


@router.post("/portal")
async def create_portal(
    contract: PortalRequest | None = None,
    principal: Principal = Depends(require_scope("account")),
):
    try:
        if contract is None or (billing_available() and not stripe_configured()):
            legacy = await create_legacy_portal(principal.user_id)
            return legacy if contract is None else {"portal_url": legacy["url"]}
        return await billing_service.portal(principal.user_id, contract.return_url, contract.idempotency_key)
    except BillingError as exc:
        raise _error(exc) from exc


@router.post("/webhook")
async def legacy_stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    if not stripe_signature:
        raise HTTPException(status_code=400, detail={"code": "webhook_signature_invalid", "message": "Stripe signature is required."})
    try:
        return accept_webhook(await request.body(), stripe_signature)
    except BillingError as exc:
        raise _error(exc) from exc


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature")):
    if not stripe_signature:
        raise HTTPException(status_code=400, detail={"code": "webhook_signature_invalid", "message": "Stripe signature is required."})
    payload = await request.body()
    try:
        return webhook_processor.process(payload, stripe_signature)
    except BillingError as exc:
        raise _error(exc) from exc
