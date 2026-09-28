"""Versioned, device-neutral perception ingress.

Adapters normalize observations here; this boundary never executes an action.
Identity comes from the authenticated principal, artifact ownership is checked
out of band, and uncertain/high-impact intent requires a distinct confirmation.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import db
from app.auth import Principal, require_scope
from app.persistence import connect
from app.uploads import get_upload, retain_upload_until

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_PERCEPTION_EVENT_BYTES = 32 * 1024
LOW_CONFIDENCE_THRESHOLD = 0.75
_EVENT_ID = re.compile(r"^pev_[A-Za-z0-9_-]{12,96}$")


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PerceptionClient(StrictContract):
    client_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$")
    device_class: Literal["phone", "web", "desktop", "headset", "wearable", "gaming", "assistive", "unknown"]
    adapter_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    adapter_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+(?:\.[0-9]+){0,3}$")


class PerceptionContext(StrictContract):
    project_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    conversation_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^mgc_[A-Za-z0-9_-]+$")
    surface: Optional[str] = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class PerceptionConsent(StrictContract):
    captured: Literal[True]
    purpose: Literal["conversation", "accessibility", "command-draft", "context"]
    retention_seconds: int = Field(ge=300, le=30 * 24 * 60 * 60)
    biometric_processing: bool = False


class IntentProvenance(StrictContract):
    transcript: Optional[str] = Field(default=None, max_length=10_000)
    adapter_transform: Literal["none", "speech-to-text", "gesture-map", "classifier", "neural-decoder"]

    @field_validator("transcript")
    @classmethod
    def safe_transcript(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any((ord(char) < 32 and char not in "\t\n\r") or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("Intent provenance contains unsafe controls.")
        return value


class PerceptionIntent(StrictContract):
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9.-]*$")
    impact: Literal["none", "low", "high"] = "none"
    provenance: IntentProvenance


class PerceptionEventContract(StrictContract):
    schema_version: Literal["magistrate.perception-event.v1"]
    event_id: str = Field(pattern=r"^pev_[A-Za-z0-9_-]{12,96}$")
    client: PerceptionClient
    modality: Literal["text", "voice", "image", "gesture", "ambient", "spatial", "subvocal", "neural"]
    observed_at_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    context: PerceptionContext
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    consent: PerceptionConsent
    artifact_ref: Optional[str] = Field(default=None, min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    intent: PerceptionIntent

    @model_validator(mode="after")
    def validate_semantics(self) -> "PerceptionEventContract":
        if self.modality in {"image", "ambient", "spatial"} and not self.artifact_ref:
            raise ValueError("This modality requires an owner-scoped artifact reference.")
        if self.modality in {"neural", "subvocal"} and not self.consent.biometric_processing:
            raise ValueError("Biometric processing consent is required for this modality.")
        if self.intent.provenance.adapter_transform == "neural-decoder" and self.modality != "neural":
            raise ValueError("Neural decoding provenance requires neural modality.")
        return self


class PerceptionConfirmationContract(StrictContract):
    schema_version: Literal["magistrate.perception-confirmation.v1"]
    revision: Literal[1]
    confirmed: Literal[True]


def _connect():
    # The ordered application migration is the sole schema authority.
    db.init_db()
    connection = connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _authorization(event: PerceptionEventContract) -> tuple[str, Optional[str]]:
    if event.confidence < LOW_CONFIDENCE_THRESHOLD:
        return "confirmation-required", "low-confidence"
    if event.intent.impact == "high":
        return "confirmation-required", "high-impact"
    if event.modality in {"neural", "subvocal"}:
        return "confirmation-required", "uncertain-biometric-input"
    return "draft", None


def _public(row: Any) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        "schema_version": "magistrate.perception-result.v1",
        "event_id": row["event_id"],
        "principal": {"id": row["owner_user_id"]},
        "client": payload["client"], "modality": row["modality"],
        "observed_at_ms": row["observed_at_ms"], "context": payload["context"],
        "confidence": row["confidence"], "consent": payload["consent"],
        "artifact_ref": row["artifact_ref"], "intent": payload["intent"],
        "authorization": {
            "state": row["authorization_state"], "reason": row["reason"],
            "revision": row["revision"], "executes_action": False,
        },
        "retention": {"expires_at": row["expires_at"]},
    }


router = APIRouter(prefix="/api/v1/perception", tags=["Perception protocol"])


@router.post("/events")
def ingest_perception_event(
    event: PerceptionEventContract,
    principal: Principal = Depends(require_scope("read")),
):
    now_ms = int(time.time_ns() // 1_000_000)
    if event.observed_at_ms > now_ms + 5 * 60 * 1000 or event.observed_at_ms < now_ms - 30 * 24 * 60 * 60 * 1000:
        raise HTTPException(status_code=422, detail="Perception timestamp is outside the accepted window.")
    if event.artifact_ref and not get_upload(principal.user_id, event.artifact_ref):
        raise HTTPException(status_code=404, detail="Perception artifact is unavailable.")
    state, reason = _authorization(event)
    payload = event.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    now = int(time.time())
    expires_at = now + event.consent.retention_seconds
    with _connect() as connection:
        connection.execute("DELETE FROM perception_events WHERE expires_at<=?", (now,))
        existing = connection.execute(
            "SELECT * FROM perception_events WHERE event_id=? AND owner_user_id=?",
            (event.event_id, principal.user_id),
        ).fetchone()
        if existing:
            if existing["payload_json"] != canonical:
                raise HTTPException(status_code=409, detail="Perception event identity is bound to different input.")
            if event.artifact_ref and not retain_upload_until(
                principal.user_id, event.artifact_ref, existing["expires_at"], connection=connection,
            ):
                raise HTTPException(status_code=404, detail="Perception artifact is unavailable.")
            result = _public(existing)
            result["duplicate"] = True
            return result
        if event.artifact_ref and not retain_upload_until(
            principal.user_id, event.artifact_ref, expires_at, connection=connection,
        ):
            raise HTTPException(status_code=404, detail="Perception artifact is unavailable.")
        connection.execute("""INSERT INTO perception_events
            (event_id,owner_user_id,payload_json,modality,confidence,intent_kind,impact,
             artifact_ref,authorization_state,reason,observed_at_ms,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event.event_id, principal.user_id, canonical, event.modality, event.confidence,
             event.intent.kind, event.intent.impact, event.artifact_ref, state, reason,
             event.observed_at_ms, now, expires_at))
        row = connection.execute(
            "SELECT * FROM perception_events WHERE event_id=? AND owner_user_id=?",
            (event.event_id, principal.user_id),
        ).fetchone()
    return _public(row)


@router.post("/events/{event_id}/confirm")
def confirm_perception_intent(
    event_id: str,
    confirmation: PerceptionConfirmationContract,
    principal: Principal = Depends(require_scope("command")),
):
    if not _EVENT_ID.fullmatch(event_id):
        raise HTTPException(status_code=404, detail="Perception event not found.")
    now = int(time.time())
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM perception_events WHERE event_id=? AND owner_user_id=? AND expires_at>?", (event_id, principal.user_id, now)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Perception event not found.")
        if row["authorization_state"] == "confirmed":
            return _public(row)
        connection.execute("UPDATE perception_events SET authorization_state='confirmed', reason='explicit-user-confirmation', revision=revision+1, confirmed_at=? WHERE event_id=? AND owner_user_id=?", (now, event_id, principal.user_id))
        updated = connection.execute(
            "SELECT * FROM perception_events WHERE event_id=? AND owner_user_id=?",
            (event_id, principal.user_id),
        ).fetchone()
    return _public(updated)
