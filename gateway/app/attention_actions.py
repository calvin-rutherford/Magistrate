"""Bounded, authenticated actions for concrete Firstmate Attention decisions.

This module deliberately does not turn notification acknowledgement into
authority.  It supports only a Firstmate ``needs-decision`` status record whose
exact task and decision key are present in the live snapshot.  The action
records the captain's approve/reject choice through Firstmate's keyed captain
hold intake; it does not expose general provider, destructive, or
security-sensitive automation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from app import db

ACTION_SCHEMA = "attention-action.v1"
CONFIRMATION_TTL_SECONDS = 300
SAFE_ACTIONS = ("approve", "reject")
# Matches fm-captain-hold's privacy-safe task-id contract exactly.
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
# These words identify requests outside this intentionally narrow MVP seam.
RISK_PATTERN = re.compile(
    r"\b(delete|destroy|deploy|merge|reset|credential|secret|password|token|"
    r"security|permission|production|public|irreversible|cannot be undone)\b",
    re.IGNORECASE,
)


class AttentionActionError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def _safe_text(value: Any, maximum: int = 200) -> str:
    return value.strip()[:maximum] if isinstance(value, str) else ""


def _action_secret() -> bytes:
    # The secret is server-only and already required by the Gateway's DB
    # protection contract.  An explicit fallback is only useful in test/dev;
    # production startup cannot reach this without MAGISTRATE_SECRET_KEY.
    return os.getenv("MAGISTRATE_SECRET_KEY", "attention-action-dev-secret").encode("utf-8")


def action_key_for(item: Dict[str, Any]) -> str:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    material = "\0".join([
        "attention-action.v1",
        "firstmate",
        _safe_text(context.get("task_id")),
        _safe_text(context.get("decision_key")),
        _safe_text(item.get("revision")),
    ])
    digest = hmac.new(_action_secret(), material.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"aa1_{digest}"


def risk_reason(item: Dict[str, Any]) -> Optional[str]:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    values = [item.get("title"), item.get("subtitle"), context.get("summary"), context.get("risk")]
    explicit_risk = any(context.get(name) is True or item.get(name) is True for name in (
        "destructive", "irreversible", "security_sensitive", "security_sensitive_request",
    )) or item.get("reversible") is False or context.get("risk_level") in {"high", "destructive", "security-sensitive"}
    if explicit_risk or any(RISK_PATTERN.search(value) for value in values if isinstance(value, str)):
        return "This decision is outside the safe Attention action boundary."
    return None


def action_for_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a non-authoritative presentation contract from a live item."""
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    task_id = _safe_text(context.get("task_id"))
    decision_key = _safe_text(context.get("decision_key"))
    if (
        item.get("provider") != "firstmate"
        or item.get("notification_kind") != "captain_question"
        or item.get("status") != "needs-decision"
        or not task_id
        or not decision_key
        or not TASK_ID_PATTERN.fullmatch(task_id)
        or not TASK_ID_PATTERN.fullmatch(decision_key)
    ):
        return None
    blocked_reason = risk_reason(item)
    return {
        "schema_version": ACTION_SCHEMA,
        "action_key": action_key_for(item),
        "decision_key": decision_key,
        "source_revision": _safe_text(item.get("revision")),
        "target": {"provider": "firstmate", "task_id": task_id, "decision_key": decision_key},
        "allowed_actions": [] if blocked_reason else list(SAFE_ACTIONS),
        "confirmation_required": True,
        # This endpoint records a keyed captain-hold answer; it does not invoke
        # an external provider operation. A later Firstmate hold may supersede
        # it, so the record operation is reversible at the governance layer.
        "consequence": "Records this choice against the exact Firstmate decision and closes its current captain hold; it does not perform an external provider operation.",
        "reversible": True,
        "status": "unsupported" if blocked_reason else "available",
        "reason": blocked_reason,
    }


def _action_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    action = action_for_item(item)
    if not action:
        raise AttentionActionError("unsupported", "This Attention item does not expose an approved action boundary.")
    return action


def _find_live_item(items: list[Dict[str, Any]], action_key: str, target_id: str) -> Dict[str, Any]:
    for item in items:
        action = action_for_item(item)
        if action and action["action_key"] == action_key:
            target = action["target"]
            if target["task_id"] != target_id:
                raise AttentionActionError("mismatch", "The requested target does not match the server-issued action key.")
            if action.get("reason"):
                raise AttentionActionError("unsupported_risk", action["reason"])
            return item
    raise AttentionActionError("stale", "This Attention decision is no longer current or has already been resolved.")


def _decode_outcome(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    outcome = dict(row)
    outcome["evidence"] = json.loads(outcome.pop("evidence_json"))
    return outcome


def _outcome_row(action_key: str, user_id: str) -> Optional[Dict[str, Any]]:
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM attention_action_outcomes WHERE action_key=? AND user_id=?", (action_key, user_id)).fetchone()
    return _decode_outcome(row)


def outcome_for_item(item_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM attention_action_outcomes WHERE item_id=? AND user_id=? ORDER BY updated_at DESC LIMIT 1", (item_id, user_id)).fetchone()
    return _decode_outcome(row)


def _public_outcome(row: Dict[str, Any], *, idempotent: bool = False) -> Dict[str, Any]:
    return {
        "schema_version": ACTION_SCHEMA,
        "action_key": row["action_key"],
        "item_id": row.get("item_id") or None,
        "decision_key": row["decision_key"],
        "action": row["action"],
        "target": {"provider": row["provider"], "task_id": row["target_id"], "decision_key": row["decision_key"]},
        "status": row["status"],
        "evidence": row["evidence"],
        "timestamp": row["updated_at"],
        "idempotent": idempotent,
    }


def _confirmation_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _store_confirmation(action: Dict[str, Any], user_id: str, session_id: str, selected_action: str, target_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO attention_action_confirmations(confirmation_hash,action_key,user_id,actor_session_id,action,target_id,expires_at) VALUES(?,?,?,?,?,?,?)",
            (_confirmation_hash(token), action["action_key"], user_id, session_id, selected_action, target_id, now + CONFIRMATION_TTL_SECONDS),
        )
    return token


def prepare_confirmation(items: list[Dict[str, Any]], action_key: str, selected_action: str, target_id: str, user_id: str, session_id: str) -> Dict[str, Any]:
    if selected_action not in SAFE_ACTIONS:
        raise AttentionActionError("invalid_action", "Only approve or reject is supported.", 422)
    item = _find_live_item(items, action_key, target_id)
    action = _action_from_item(item)
    if selected_action not in action["allowed_actions"]:
        raise AttentionActionError("unsupported", "That action is not available for this decision.")
    token = _store_confirmation(action, user_id, session_id, selected_action, target_id)
    return {
        "schema_version": ACTION_SCHEMA,
        "status": "confirmation_required",
        "action_key": action_key,
        "action": selected_action,
        "decision_key": action["decision_key"],
        "source_revision": action["source_revision"],
        "target": action["target"],
        "consequence": action["consequence"],
        "reversible": action["reversible"],
        "confirmation_token": token,
        "expires_at": int(time.time()) + CONFIRMATION_TTL_SECONDS,
    }


def _claim_pending(action: Dict[str, Any], user_id: str, session_id: str, selected_action: str, target_id: str) -> Optional[Dict[str, Any]]:
    now = int(time.time())
    evidence = {
        "provider": "firstmate",
        "decision_key": action["decision_key"],
        "item_id": action.get("item_id", ""),
        "target_id": target_id,
        "source_revision": action.get("source_revision", ""),
        "operation": "captain-hold-answer",
    }
    db.init_db()
    try:
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO attention_action_outcomes(action_key,user_id,actor_session_id,item_id,decision_key,action,provider,target_id,source_revision,status,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (action["action_key"], user_id, session_id, action.get("item_id", ""), action["decision_key"], selected_action, "firstmate", target_id, _safe_text(action.get("source_revision")), "pending", json.dumps(evidence, sort_keys=True), now, now),
            )
        return None
    except sqlite3.IntegrityError:
        return _outcome_row(action["action_key"], user_id)


async def execute_firstmate_action(task_id: str, selected_action: str, fm_home: str) -> Dict[str, Any]:
    """Resolve one captain hold through Firstmate's existing keyed intake."""
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise AttentionActionError("mismatch", "The Firstmate target identity is invalid.")
    decision = f"{selected_action.capitalize()}d by Captain via Magistrate Attention."
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(prefix="magistrate-attention-", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(decision)
        command = os.path.join(fm_home, "bin", "fm-captain-hold.sh")
        process = await asyncio.create_subprocess_exec(
            command, "answer", task_id, "--decision-file", temp_path,
            cwd=fm_home, env={**os.environ, "FM_HOME": fm_home, "FM_ROOT_OVERRIDE": fm_home},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        return_code = await asyncio.wait_for(process.wait(), timeout=30)
        return {"ok": return_code == 0}
    except (OSError, asyncio.TimeoutError):
        if 'process' in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        return {"ok": False}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


async def execute_confirmation(
    items: list[Dict[str, Any]], action_key: str, selected_action: str, target_id: str,
    confirmation_token: str, user_id: str, session_id: str, fm_home: str,
    executor: Optional[Callable[[str, str, str], Awaitable[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    if selected_action not in SAFE_ACTIONS:
        raise AttentionActionError("invalid_action", "Only approve or reject is supported.", 422)
    existing = _outcome_row(action_key, user_id)
    if existing:
        if existing["action"] != selected_action or existing["target_id"] != target_id:
            raise AttentionActionError("replay_mismatch", "This action key has already been used for a different request.")
        return _public_outcome(existing, idempotent=True)
    item = _find_live_item(items, action_key, target_id)
    action = _action_from_item(item)
    if selected_action not in action["allowed_actions"]:
        raise AttentionActionError("unsupported", "That action is not available for this decision.")
    if not isinstance(confirmation_token, str) or not confirmation_token:
        raise AttentionActionError("confirmation_required", "Explicit confirmation is required before execution.", 400)
    try:
        confirmation_hash = _confirmation_hash(confirmation_token)
    except (UnicodeEncodeError, AttributeError):
        raise AttentionActionError("confirmation_invalid", "Confirmation is missing, expired, already used, or does not match this target.", 409) from None
    db.init_db()
    now = int(time.time())
    with sqlite3.connect(db.DB_PATH) as conn:
        row = conn.execute(
            "SELECT action_key,user_id,actor_session_id,action,target_id,expires_at,used_at FROM attention_action_confirmations WHERE confirmation_hash=?",
            (confirmation_hash,),
        ).fetchone()
        if not row or row[1] != user_id or row[2] != session_id or row[0] != action_key or row[3] != selected_action or row[4] != target_id or row[5] < now or row[6] is not None:
            raise AttentionActionError("confirmation_invalid", "Confirmation is missing, expired, already used, or does not match this target.", 409)
        conn.execute("UPDATE attention_action_confirmations SET used_at=? WHERE confirmation_hash=? AND used_at IS NULL", (now, confirmation_hash))
    action["source_revision"] = _safe_text(item.get("revision"))
    action["item_id"] = _safe_text(item.get("id"))
    previous = _claim_pending(action, user_id, session_id, selected_action, target_id)
    if previous:
        return _public_outcome(previous, idempotent=True)
    execution = await (executor or execute_firstmate_action)(target_id, selected_action, fm_home)
    status = "succeeded" if execution.get("ok") else "failed"
    evidence = {
        "provider": "firstmate", "decision_key": action["decision_key"], "target_id": target_id,
        "source_revision": _safe_text(item.get("revision")), "operation": "captain-hold-answer",
        "recorded": status == "succeeded",
    }
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE attention_action_outcomes SET status=?, evidence_json=?, updated_at=? WHERE action_key=? AND user_id=?", (status, json.dumps(evidence, sort_keys=True), int(time.time()), action_key, user_id))
    outcome = _outcome_row(action_key, user_id)
    return _public_outcome(outcome) if outcome else {"status": status, "action_key": action_key, "evidence": evidence}
