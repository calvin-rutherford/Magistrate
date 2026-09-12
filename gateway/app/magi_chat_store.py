"""Additive SQLite authority for provider-native Magi conversations.

These tables are intentionally separate from the legacy captain/terminal
schema. They contain only user-visible messages, truthful lifecycle state,
bounded attachment references, and delivery/idempotency bookkeeping.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
import sqlite3
import threading
import time
from typing import Any, Sequence

from app import db

MAGI_NATIVE_SCHEMA = "magi.native-chat.v1"
MAX_MAGI_HISTORY_MESSAGES = 200
MAX_MAGI_CONTEXT_MESSAGES = 40
MAX_ATTACHMENTS_PER_MESSAGE = 10
_BUSY_TIMEOUT_SECONDS = 10
_VALID_STATUSES = frozenset({"pending", "completed", "failed", "cancelled"})
_VALID_SOURCES = frozenset({"text", "voice"})
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_DB_PATHS: set[str] = set()


class MagiChatNotFound(LookupError):
    pass


class MagiChatConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedSubmission:
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    turn_id: str
    attempt: int
    claimed: bool
    duplicate: bool
    status: str


def _now_ms() -> int:
    return int(time.time_ns() // 1_000_000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _connect() -> sqlite3.Connection:
    path = str(db.DB_PATH)
    if path not in _INITIALIZED_DB_PATHS:
        with _SCHEMA_LOCK:
            if path not in _INITIALIZED_DB_PATHS:
                db.init_db()
                _INITIALIZED_DB_PATHS.add(path)
    connection = sqlite3.connect(db.DB_PATH, timeout=_BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _canonical_attachment_records(attachments: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError("A message may include at most 10 attachments.")
    records: list[dict[str, Any]] = []
    for attachment in attachments:
        upload_id = attachment.get("upload_id")
        name = attachment.get("filename", attachment.get("name"))
        media_type = attachment.get("media_type")
        size = attachment.get("size")
        if (
            not isinstance(upload_id, str) or not 16 <= len(upload_id) <= 64
            or not upload_id.replace("-", "").replace("_", "").isalnum()
            or not isinstance(name, str) or not name or len(name) > 160
            or not isinstance(media_type, str) or not media_type or len(media_type) > 128
            or not isinstance(size, int) or isinstance(size, bool)
            or size < 0 or size > 25 * 1024 * 1024
        ):
            raise ValueError("Invalid native Magi attachment metadata.")
        records.append({
            "id": upload_id,
            "upload_id": upload_id,
            "name": name,
            "media_type": media_type,
            "size": size,
            "url": f"/api/v1/uploads/{upload_id}",
        })
    return records


def _attachment_json(attachments: Sequence[dict[str, Any]] | None) -> str:
    return json.dumps(
        _canonical_attachment_records(attachments),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_attachments(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
        return _canonical_attachment_records(value if isinstance(value, list) else [])
    except (ValueError, TypeError, json.JSONDecodeError):
        return []


def _append_change(connection: sqlite3.Connection, conversation_id: str, message_id: str, revision: int, now: int) -> int:
    latest = connection.execute(
        "SELECT COALESCE(MAX(change_sequence), 0) FROM magi_message_changes WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()[0]
    sequence = int(latest) + 1
    connection.execute(
        """INSERT INTO magi_message_changes
           (conversation_id, change_sequence, message_id, message_revision, changed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (conversation_id, sequence, message_id, revision, now),
    )
    return sequence


def _increment_diagnostics(
    connection: sqlite3.Connection,
    owner_user_id: str,
    **increments: int,
) -> None:
    allowed = {
        "magi_messages_submitted", "magi_messages_completed", "magi_messages_failed",
        "magi_duplicate_submissions", "magi_retries", "magi_tool_calls",
        "legacy_chat_reads", "terminal_chat_reads", "pi_ownership_chat_reads",
    }
    if not increments or not set(increments).issubset(allowed):
        raise ValueError("Unknown Magi diagnostic counter.")
    connection.execute(
        "INSERT OR IGNORE INTO magi_chat_diagnostics (owner_user_id, updated_at) VALUES (?, ?)",
        (owner_user_id, _now_ms()),
    )
    assignments = ", ".join(f"{name} = {name} + ?" for name in increments)
    values = [max(0, int(value)) for value in increments.values()]
    connection.execute(
        f"UPDATE magi_chat_diagnostics SET {assignments}, updated_at = ? WHERE owner_user_id = ?",
        (*values, _now_ms(), owner_user_id),
    )


def record_compatibility_read(owner_user_id: str, counter: str) -> None:
    """Increment one content-free legacy migration counter."""
    with _connect() as connection:
        _increment_diagnostics(connection, owner_user_id, **{counter: 1})


def _record_completion_metrics(
    connection: sqlite3.Connection,
    owner_user_id: str,
    *,
    latency_ms: int,
    response_characters: int,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO magi_chat_diagnostics (owner_user_id, updated_at) VALUES (?, ?)",
        (owner_user_id, _now_ms()),
    )
    connection.execute(
        """UPDATE magi_chat_diagnostics
           SET magi_messages_completed = magi_messages_completed + 1,
               magi_completion_latency_ms = ?,
               magi_completion_latency_total_ms = magi_completion_latency_total_ms + ?,
               magi_completion_latency_max_ms = MAX(magi_completion_latency_max_ms, ?),
               magi_response_characters = magi_response_characters + ?,
               magi_response_characters_last = ?,
               magi_response_characters_max = MAX(magi_response_characters_max, ?),
               updated_at = ?
           WHERE owner_user_id = ?""",
        (
            latency_ms, latency_ms, latency_ms,
            response_characters, response_characters, response_characters,
            _now_ms(), owner_user_id,
        ),
    )


def _conversation_row(connection: sqlite3.Connection, owner_user_id: str, conversation_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM magi_conversations WHERE id = ? AND owner_user_id = ?",
        (conversation_id, owner_user_id),
    ).fetchone()
    if row is None:
        # Deliberately do not reveal whether another tenant owns this id.
        raise MagiChatNotFound("Native Magi conversation not found.")
    return row


def _default_conversation(connection: sqlite3.Connection, owner_user_id: str, *, create: bool) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT * FROM magi_conversations WHERE owner_user_id = ? AND is_default = 1",
        (owner_user_id,),
    ).fetchone()
    if row is not None or not create:
        return row
    now = _now_ms()
    conversation_id = _new_id("mgc")
    try:
        connection.execute(
            """INSERT INTO magi_conversations
               (id, owner_user_id, is_default, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?)""",
            (conversation_id, owner_user_id, now, now),
        )
    except sqlite3.IntegrityError:
        # Another process may have created the principal's default after the
        # read. The partial unique index makes this retry deterministic.
        row = connection.execute(
            "SELECT * FROM magi_conversations WHERE owner_user_id = ? AND is_default = 1",
            (owner_user_id,),
        ).fetchone()
        if row is None:
            raise
        return row
    return connection.execute(
        "SELECT * FROM magi_conversations WHERE id = ?", (conversation_id,),
    ).fetchone()


def _public_conversation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _public_message(row: sqlite3.Row) -> dict[str, Any]:
    status = row["status"] if row["status"] in _VALID_STATUSES else "failed"
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "turn_id": row["turn_id"],
        "client_message_id": row["client_message_id"],
        "reply_to_message_id": row["reply_to_message_id"],
        "role": row["role"],
        "content": row["content"],
        "status": status,
        "source": row["source"],
        "sequence_index": row["sequence_index"],
        "revision": row["revision"],
        "attachments": _decode_attachments(row["attachments_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _submission_rows(connection: sqlite3.Connection, owner_user_id: str, client_message_id: str) -> tuple[sqlite3.Row, sqlite3.Row] | None:
    user = connection.execute(
        """SELECT * FROM magi_messages
           WHERE owner_user_id = ? AND client_message_id = ? AND role = 'user'""",
        (owner_user_id, client_message_id),
    ).fetchone()
    if user is None:
        return None
    assistant = connection.execute(
        """SELECT * FROM magi_messages
           WHERE owner_user_id = ? AND conversation_id = ? AND turn_id = ? AND role = 'assistant'""",
        (owner_user_id, user["conversation_id"], user["turn_id"]),
    ).fetchone()
    if assistant is None:
        raise MagiChatConflict("Native Magi submission is missing its reserved assistant message.")
    return user, assistant


class MagiChatStore:
    """Transactional native conversation store with concurrent idempotency."""

    def prepare_submission(
        self,
        owner_user_id: str,
        client_message_id: str,
        content: str,
        *,
        conversation_id: str | None = None,
        source: str = "text",
        attachments: Sequence[dict[str, Any]] | None = None,
        retry_failed: bool = False,
    ) -> PreparedSubmission:
        if source not in _VALID_SOURCES:
            raise ValueError("Native Magi message source must be text or voice.")
        attachments_json = _attachment_json(attachments)
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            conversation = (
                _conversation_row(connection, owner_user_id, conversation_id)
                if conversation_id else _default_conversation(connection, owner_user_id, create=True)
            )
            assert conversation is not None
            existing = _submission_rows(connection, owner_user_id, client_message_id)
            if existing:
                user, assistant = existing
                _increment_diagnostics(connection, owner_user_id, magi_duplicate_submissions=1)
                if (
                    user["conversation_id"] != conversation["id"]
                    or user["content"] != content
                    or user["source"] != source
                    or user["attachments_json"] != attachments_json
                ):
                    raise MagiChatConflict(
                        "That client message id is already bound to a different native Magi submission."
                    )
                claimed = False
                if retry_failed and assistant["status"] == "failed":
                    now = _now_ms()
                    changed = connection.execute(
                        """UPDATE magi_messages
                           SET status = 'pending', content = '', error_code = NULL,
                               attempt_count = attempt_count + 1,
                               revision = revision + 1, updated_at = ?
                           WHERE id = ? AND owner_user_id = ? AND status = 'failed'""",
                        (now, assistant["id"], owner_user_id),
                    ).rowcount
                    if changed:
                        assistant = connection.execute(
                            "SELECT * FROM magi_messages WHERE id = ?", (assistant["id"],),
                        ).fetchone()
                        _append_change(
                            connection, conversation["id"], assistant["id"], assistant["revision"], now,
                        )
                        _increment_diagnostics(connection, owner_user_id, magi_retries=1)
                        claimed = True
                connection.commit()
                return PreparedSubmission(
                    conversation_id=conversation["id"],
                    user_message_id=user["id"],
                    assistant_message_id=assistant["id"],
                    turn_id=user["turn_id"],
                    attempt=assistant["attempt_count"],
                    claimed=claimed,
                    duplicate=True,
                    status=assistant["status"],
                )

            now = _now_ms()
            maximum = connection.execute(
                "SELECT COALESCE(MAX(sequence_index), -1) FROM magi_messages WHERE conversation_id = ?",
                (conversation["id"],),
            ).fetchone()[0]
            user_sequence = int(maximum) + 1
            turn_id = _new_id("mgt")
            user_id = _new_id("mgm")
            assistant_id = _new_id("mgm")
            connection.execute(
                """INSERT INTO magi_messages
                   (id, conversation_id, owner_user_id, turn_id, role, content, status, source,
                    client_message_id, reply_to_message_id, attachments_json, sequence_index,
                    revision, attempt_count, error_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'user', ?, 'completed', ?, ?, NULL, ?, ?, 1, 1, NULL, ?, ?)""",
                (
                    user_id, conversation["id"], owner_user_id, turn_id, content, source,
                    client_message_id, attachments_json, user_sequence, now, now,
                ),
            )
            connection.execute(
                """INSERT INTO magi_messages
                   (id, conversation_id, owner_user_id, turn_id, role, content, status, source,
                    client_message_id, reply_to_message_id, attachments_json, sequence_index,
                    revision, attempt_count, error_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'assistant', '', 'pending', 'magi-native',
                           NULL, ?, '[]', ?, 1, 1, NULL, ?, ?)""",
                (
                    assistant_id, conversation["id"], owner_user_id, turn_id,
                    user_id, user_sequence + 1, now, now,
                ),
            )
            connection.execute(
                "UPDATE magi_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation["id"]),
            )
            _append_change(connection, conversation["id"], user_id, 1, now)
            _append_change(connection, conversation["id"], assistant_id, 1, now)
            _increment_diagnostics(connection, owner_user_id, magi_messages_submitted=1)
            connection.commit()
            return PreparedSubmission(
                conversation_id=conversation["id"],
                user_message_id=user_id,
                assistant_message_id=assistant_id,
                turn_id=turn_id,
                attempt=1,
                claimed=True,
                duplicate=False,
                status="pending",
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def context_before(
        self,
        owner_user_id: str,
        conversation_id: str,
        user_message_id: str,
        *,
        limit: int = MAX_MAGI_CONTEXT_MESSAGES,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_MAGI_CONTEXT_MESSAGES:
            raise ValueError("Native Magi context limit is invalid.")
        with _connect() as connection:
            _conversation_row(connection, owner_user_id, conversation_id)
            current = connection.execute(
                "SELECT sequence_index FROM magi_messages WHERE id = ? AND owner_user_id = ? AND role = 'user'",
                (user_message_id, owner_user_id),
            ).fetchone()
            if current is None:
                raise MagiChatNotFound("Native Magi user message not found.")
            rows = connection.execute(
                """SELECT * FROM magi_messages
                   WHERE owner_user_id = ? AND conversation_id = ? AND sequence_index < ?
                     AND status = 'completed' AND content != ''
                   ORDER BY sequence_index DESC LIMIT ?""",
                (owner_user_id, conversation_id, current["sequence_index"], limit),
            ).fetchall()
        return [_public_message(row) for row in reversed(rows)]

    def complete_submission(
        self,
        owner_user_id: str,
        assistant_message_id: str,
        attempt: int,
        content: str,
        *,
        latency_ms: int,
    ) -> bool:
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM magi_messages WHERE id = ? AND owner_user_id = ? AND role = 'assistant'",
                (assistant_message_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise MagiChatNotFound("Native Magi assistant message not found.")
            if row["status"] != "pending" or row["attempt_count"] != attempt:
                connection.commit()
                return False
            revision = row["revision"] + 1
            connection.execute(
                """UPDATE magi_messages SET content = ?, status = 'completed', error_code = NULL,
                   revision = ?, updated_at = ? WHERE id = ?""",
                (content, revision, now, assistant_message_id),
            )
            connection.execute(
                "UPDATE magi_conversations SET updated_at = ? WHERE id = ?",
                (now, row["conversation_id"]),
            )
            _append_change(connection, row["conversation_id"], assistant_message_id, revision, now)
            _record_completion_metrics(
                connection, owner_user_id,
                latency_ms=max(0, int(latency_ms)), response_characters=len(content),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_submission(
        self,
        owner_user_id: str,
        assistant_message_id: str,
        attempt: int,
        error_code: str,
        *,
        tool_calls: int = 0,
    ) -> bool:
        safe_code = error_code if error_code and len(error_code) <= 64 else "provider_failure"
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM magi_messages WHERE id = ? AND owner_user_id = ? AND role = 'assistant'",
                (assistant_message_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise MagiChatNotFound("Native Magi assistant message not found.")
            if row["status"] != "pending" or row["attempt_count"] != attempt:
                connection.commit()
                return False
            revision = row["revision"] + 1
            connection.execute(
                """UPDATE magi_messages SET content = '', status = 'failed', error_code = ?,
                   revision = ?, updated_at = ? WHERE id = ?""",
                (safe_code, revision, now, assistant_message_id),
            )
            connection.execute(
                "UPDATE magi_conversations SET updated_at = ? WHERE id = ?",
                (now, row["conversation_id"]),
            )
            _append_change(connection, row["conversation_id"], assistant_message_id, revision, now)
            _increment_diagnostics(
                connection, owner_user_id,
                magi_messages_failed=1, magi_tool_calls=max(0, int(tool_calls)),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def recover_orphaned_pending(self) -> int:
        """Fail provider attempts that cannot survive a completed process restart.

        Call exactly once during application startup, before requests are
        accepted. Mobile disconnects remain attached to their shielded task;
        only rows left pending across process lifetime are made explicitly
        retryable here.
        """
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM magi_messages WHERE role = 'assistant' AND status = 'pending'"
            ).fetchall()
            failures: dict[str, int] = {}
            for row in rows:
                revision = row["revision"] + 1
                connection.execute(
                    """UPDATE magi_messages SET content = '', status = 'failed',
                       error_code = 'server_restart', revision = ?, updated_at = ?
                       WHERE id = ? AND status = 'pending'""",
                    (revision, now, row["id"]),
                )
                connection.execute(
                    "UPDATE magi_conversations SET updated_at = ? WHERE id = ?",
                    (now, row["conversation_id"]),
                )
                _append_change(connection, row["conversation_id"], row["id"], revision, now)
                failures[row["owner_user_id"]] = failures.get(row["owner_user_id"], 0) + 1
            for owner_user_id, count in failures.items():
                _increment_diagnostics(connection, owner_user_id, magi_messages_failed=count)
            connection.commit()
            return len(rows)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel_submission(self, owner_user_id: str, client_message_id: str) -> dict[str, Any]:
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pair = _submission_rows(connection, owner_user_id, client_message_id)
            if pair is None:
                raise MagiChatNotFound("Native Magi submission not found.")
            user, assistant = pair
            if assistant["status"] == "pending":
                revision = assistant["revision"] + 1
                connection.execute(
                    """UPDATE magi_messages SET content = '', status = 'cancelled', error_code = NULL,
                       revision = ?, updated_at = ? WHERE id = ?""",
                    (revision, now, assistant["id"]),
                )
                _append_change(connection, user["conversation_id"], assistant["id"], revision, now)
                connection.execute(
                    "UPDATE magi_conversations SET updated_at = ? WHERE id = ?",
                    (now, user["conversation_id"]),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.submission(owner_user_id, client_message_id)

    def submission(self, owner_user_id: str, client_message_id: str) -> dict[str, Any]:
        with _connect() as connection:
            pair = _submission_rows(connection, owner_user_id, client_message_id)
            if pair is None:
                raise MagiChatNotFound("Native Magi submission not found.")
            user, assistant = pair
            conversation = _conversation_row(connection, owner_user_id, user["conversation_id"])
        return {
            "schema_version": MAGI_NATIVE_SCHEMA,
            "status": assistant["status"],
            "conversation": _public_conversation(conversation),
            "user_message": _public_message(user),
            "assistant_message": _public_message(assistant),
            "messages": [_public_message(user), _public_message(assistant)],
        }

    def list_conversation(
        self,
        owner_user_id: str,
        conversation_id: str | None = None,
        *,
        before: int | None = None,
        limit: int = MAX_MAGI_HISTORY_MESSAGES,
    ) -> dict[str, Any]:
        if not 1 <= limit <= MAX_MAGI_HISTORY_MESSAGES:
            raise ValueError("Native Magi history limit is invalid.")
        if before is not None and before < 1:
            raise ValueError("Native Magi history cursor is invalid.")
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            conversation = (
                _conversation_row(connection, owner_user_id, conversation_id)
                if conversation_id else _default_conversation(connection, owner_user_id, create=True)
            )
            assert conversation is not None
            params: list[Any] = [owner_user_id, conversation["id"]]
            cursor_clause = ""
            if before is not None:
                cursor_clause = " AND sequence_index < ?"
                params.append(before)
            params.append(limit + 1)
            rows = connection.execute(
                f"""SELECT * FROM magi_messages
                    WHERE owner_user_id = ? AND conversation_id = ?{cursor_clause}
                    ORDER BY sequence_index DESC LIMIT ?""",
                params,
            ).fetchall()
            latest_change = connection.execute(
                "SELECT COALESCE(MAX(change_sequence), 0) FROM magi_message_changes WHERE conversation_id = ?",
                (conversation["id"],),
            ).fetchone()[0]
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        has_more = len(rows) > limit
        page = list(reversed(rows[:limit]))
        return {
            "schema_version": MAGI_NATIVE_SCHEMA,
            "conversation": _public_conversation(conversation),
            "conversation_id": conversation["id"],
            "messages": [_public_message(row) for row in page],
            "has_more": has_more,
            "next_before": page[0]["sequence_index"] if has_more and page else None,
            "latest_change": int(latest_change),
        }

    def replay(
        self,
        owner_user_id: str,
        conversation_id: str,
        *,
        after: int = 0,
        limit: int = MAX_MAGI_HISTORY_MESSAGES,
    ) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= MAX_MAGI_HISTORY_MESSAGES:
            raise ValueError("Native Magi replay cursor is invalid.")
        with _connect() as connection:
            conversation = _conversation_row(connection, owner_user_id, conversation_id)
            changes = connection.execute(
                """SELECT change_sequence, message_id FROM magi_message_changes
                   WHERE conversation_id = ? AND change_sequence > ?
                   ORDER BY change_sequence LIMIT ?""",
                (conversation_id, after, limit + 1),
            ).fetchall()
            selected = changes[:limit]
            messages = []
            for change in selected:
                message = connection.execute(
                    "SELECT * FROM magi_messages WHERE id = ? AND owner_user_id = ?",
                    (change["message_id"], owner_user_id),
                ).fetchone()
                if message is None:
                    raise MagiChatConflict("Native Magi replay references a missing message.")
                messages.append({**_public_message(message), "change_sequence": change["change_sequence"]})
            latest = connection.execute(
                "SELECT COALESCE(MAX(change_sequence), 0) FROM magi_message_changes WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
        return {
            "schema_version": MAGI_NATIVE_SCHEMA,
            "conversation": _public_conversation(conversation),
            "conversation_id": conversation_id,
            "messages": messages,
            "next_cursor": selected[-1]["change_sequence"] if selected else after,
            "latest_change": int(latest),
            "has_more": len(changes) > limit,
        }

    def diagnostics(self, owner_user_id: str) -> dict[str, Any]:
        with _connect() as connection:
            row = connection.execute(
                "SELECT * FROM magi_chat_diagnostics WHERE owner_user_id = ?",
                (owner_user_id,),
            ).fetchone()
        names = (
            "magi_messages_submitted", "magi_messages_completed", "magi_messages_failed",
            "magi_duplicate_submissions", "magi_completion_latency_ms",
            "magi_response_characters", "magi_retries", "magi_tool_calls",
            "legacy_chat_reads", "terminal_chat_reads", "pi_ownership_chat_reads",
        )
        metrics = {name: int(row[name]) if row else 0 for name in names}
        completed = metrics["magi_messages_completed"]
        metrics["completion_latency"] = {
            "count": completed,
            "total_ms": int(row["magi_completion_latency_total_ms"]) if row else 0,
            "max_ms": int(row["magi_completion_latency_max_ms"]) if row else 0,
            "average_ms": (
                int(row["magi_completion_latency_total_ms"]) / completed if row and completed else 0
            ),
        }
        metrics["response_characters"] = {
            "total": metrics["magi_response_characters"],
            "last": int(row["magi_response_characters_last"]) if row else 0,
            "max": int(row["magi_response_characters_max"]) if row else 0,
        }
        return {"schema_version": "magi.native-chat-diagnostics.v1", **metrics}

    def reset_diagnostics(self, owner_user_id: str) -> None:
        """Test/runner helper; conversation rows are never deleted."""
        with _connect() as connection:
            connection.execute("DELETE FROM magi_chat_diagnostics WHERE owner_user_id = ?", (owner_user_id,))
