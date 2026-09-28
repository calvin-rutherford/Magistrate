"""Transactional account erasure and tenant-retention boundaries."""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
from app.persistence import connect
import time
from pathlib import Path
from typing import Any

from app import db


class AccountDeletionError(RuntimeError):
    pass


# Directly tenant-qualified tables. Child tables without owner columns are
# removed first through owner-qualified parent subqueries below.
_DIRECT_OWNER_TABLES: tuple[tuple[str, str], ...] = (
    ("account_onboarding", "user_id"),
    ("account_credit_ledger", "owner_user_id"),
    ("execution_usage_ledger", "owner_user_id"),
    ("credit_reservations", "owner_user_id"),
    ("credit_ledger", "owner_user_id"),
    ("billing_checkout_sessions", "owner_user_id"),
    ("billing_accounts", "owner_user_id"),
    ("project_memories", "owner_user_id"),
    ("project_repositories", "owner_user_id"),
    ("projects", "owner_user_id"),
    ("workspaces", "owner_user_id"),
    ("objective_cancellation_requests", "owner_user_id"),
    ("magi_objective_submissions", "owner_user_id"),
    ("firstmate_execution_events", "owner_user_id"),
    ("firstmate_execution_objectives", "owner_user_id"),
    ("firstmate_decision_answers", "owner_user_id"),
    ("firstmate_decision_answer_confirmations", "owner_user_id"),
    ("firstmate_decision_events", "owner_user_id"),
    ("firstmate_decisions", "owner_user_id"),
    ("firstmate_decision_sources", "owner_user_id"),
    ("attention_action_confirmations", "user_id"),
    ("attention_action_outcomes", "user_id"),
    ("agent_migration_requests", "user_id"),
    ("conversation_ingest_diagnostics", "user_id"),
    ("pi_semantic_dispatches", "user_id"),
    ("activity_changes", "user_id"),
    ("activity_records", "user_id"),
    ("canonical_source_events", "user_id"),
    ("activity_sources", "user_id"),
    ("magi_chat_diagnostics", "owner_user_id"),
    ("execution_preferences", "user_id"),
    ("execution_credentials", "user_id"),
    ("provider_auth_challenges", "owner_user_id"),
    ("oauth_transactions", "principal_id"),
    ("github_app_transactions", "user_id"),
    ("github_app_installations", "user_id"),
    ("gateway_sessions", "user_id"),
    ("friend_beta_access_grants", "user_id"),
    ("user_profiles", "user_id"),
)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _stage_files(connection: sqlite3.Connection, user_id: str) -> tuple[Path, list[tuple[Path, Path]]]:
    """Atomically rename account-owned files before deleting their metadata."""
    candidates: list[Path] = []
    tables = _tables(connection)
    if "chat_uploads" in tables:
        candidates.extend(
            Path(row[0]) for row in connection.execute(
                "SELECT path FROM chat_uploads WHERE user_id = ?", (user_id,)
            ) if row[0]
        )
    profile = connection.execute(
        "SELECT avatar_url FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if profile and isinstance(profile[0], str) and profile[0].startswith("/uploads/avatars/"):
        avatar_name = profile[0].removeprefix("/uploads/avatars/")
        if avatar_name and avatar_name != "default_avatar.png" and Path(avatar_name).name == avatar_name:
            candidates.append(Path(__file__).resolve().parents[1] / "uploads" / "avatars" / avatar_name)

    root = Path(db.DB_PATH).parent / ".account-deletion"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    for index, source in enumerate(candidates):
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError:
            continue
        target = root / f"{time.time_ns()}-{index}"
        resolved.rename(target)
        staged.append((resolved, target))
    return root, staged


def _restore_files(staged: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(staged):
        if target.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)


def _delete_indirect_rows(connection: sqlite3.Connection, user_id: str, tables: set[str]) -> int:
    deleted = 0

    def execute(table: str, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        nonlocal deleted
        if table in tables:
            deleted += connection.execute(sql, parameters).rowcount

    # Cross-domain children must be removed before the Magi/activity/legacy
    # rows they reference. These deletes are repeated harmlessly by the direct
    # owner list so old SQLite deployments and PostgreSQL enforce one policy.
    for table, column in (
        ("firstmate_decision_answers", "owner_user_id"),
        ("firstmate_decision_answer_confirmations", "owner_user_id"),
        ("firstmate_decision_events", "owner_user_id"),
        ("firstmate_decisions", "owner_user_id"),
        ("firstmate_execution_events", "owner_user_id"),
        ("firstmate_execution_objectives", "owner_user_id"),
        ("pi_semantic_dispatches", "user_id"),
    ):
        execute(table, f"DELETE FROM {table} WHERE {column} = ?", (user_id,))

    # Native Magi children, including upload associations, are removed before
    # their principal-owned messages/conversations.
    execute(
        "chat_message_attachments",
        "DELETE FROM chat_message_attachments WHERE user_id = ?",
        (user_id,),
    )
    execute("chat_uploads", "DELETE FROM chat_uploads WHERE user_id = ?", (user_id,))
    execute(
        "magi_message_changes",
        """DELETE FROM magi_message_changes WHERE conversation_id IN
           (SELECT id FROM magi_conversations WHERE owner_user_id = ?)""",
        (user_id,),
    )
    execute("magi_generated_messages", "DELETE FROM magi_generated_messages WHERE owner_user_id = ?", (user_id,))
    execute("magi_messages", "DELETE FROM magi_messages WHERE owner_user_id = ?", (user_id,))
    execute("magi_conversations", "DELETE FROM magi_conversations WHERE owner_user_id = ?", (user_id,))

    # Retired conversation/Pi history remains untouched during normal runtime,
    # but a data-subject deletion must erase it.
    execute(
        "magi_additional_response_events",
        """DELETE FROM magi_additional_response_events WHERE turn_id IN
           (SELECT t.id FROM conversation_turns t JOIN conversations c ON c.id = t.conversation_id WHERE c.user_id = ?)""",
        (user_id,),
    )
    execute(
        "magi_response_events",
        """DELETE FROM magi_response_events WHERE turn_id IN
           (SELECT t.id FROM conversation_turns t JOIN conversations c ON c.id = t.conversation_id WHERE c.user_id = ?)""",
        (user_id,),
    )
    execute(
        "conversation_assistant_reservations",
        """DELETE FROM conversation_assistant_reservations WHERE turn_id IN
           (SELECT t.id FROM conversation_turns t JOIN conversations c ON c.id = t.conversation_id WHERE c.user_id = ?)""",
        (user_id,),
    )
    execute(
        "conversation_changes",
        "DELETE FROM conversation_changes WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)",
        (user_id,),
    )
    execute("conversation_messages", "DELETE FROM conversation_messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)", (user_id,))
    execute("conversation_turns", "DELETE FROM conversation_turns WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)", (user_id,))
    execute("conversations", "DELETE FROM conversations WHERE user_id = ?", (user_id,))

    # GitHub repository selections are children of owner-bound installations.
    # Remove them before installation authority and principal state.
    execute(
        "github_app_repositories",
        """DELETE FROM github_app_repositories WHERE installation_id IN
           (SELECT installation_id FROM github_app_installations WHERE user_id = ?)""",
        (user_id,),
    )

    # Provider credentials and refresh authority are indirect children. Bearer
    # sessions reference both provider families/accounts and beta grants, so
    # revoke/delete them before either authority row.
    execute("gateway_sessions", "DELETE FROM gateway_sessions WHERE user_id = ?", (user_id,))
    execute(
        "provider_refresh_tokens",
        "DELETE FROM provider_refresh_tokens WHERE family_id IN (SELECT family_id FROM provider_session_families WHERE user_id = ?)",
        (user_id,),
    )
    execute(
        "provider_assertions",
        "DELETE FROM provider_assertions WHERE connected_account_id IN (SELECT id FROM connected_accounts WHERE user_id = ?)",
        (user_id,),
    )
    execute(
        "provider_capabilities",
        "DELETE FROM provider_capabilities WHERE connected_account_id IN (SELECT id FROM connected_accounts WHERE user_id = ?)",
        (user_id,),
    )
    execute(
        "oauth_credentials",
        "DELETE FROM oauth_credentials WHERE connected_account_id IN (SELECT id FROM connected_accounts WHERE user_id = ?)",
        (user_id,),
    )
    execute("provider_session_families", "DELETE FROM provider_session_families WHERE user_id = ?", (user_id,))
    execute("connected_accounts", "DELETE FROM connected_accounts WHERE user_id = ?", (user_id,))

    # Notification tables are installed lazily and have stable user_id columns.
    for table in ("notification_events", "notification_state", "notification_preferences", "push_tokens"):
        execute(table, f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    return deleted


def delete_account(user_id: str, *, confirmation: str, fail_after_stage: bool = False) -> dict[str, Any]:
    """Erase one principal in a single DB transaction and revoke all authority.

    Files are first renamed into a private same-volume quarantine. A database
    failure restores those names; after commit, quarantine bytes are unlinked.
    """
    if confirmation != f"DELETE {user_id}":
        raise AccountDeletionError("Account deletion confirmation does not match the authenticated account.")
    db.init_db()
    connection = connect(db.DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = OFF")
    staged: list[tuple[Path, Path]] = []
    deleted = 0
    try:
        connection.execute("BEGIN EXCLUSIVE")
        exists = connection.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not exists:
            raise AccountDeletionError("Account not found.")
        _, staged = _stage_files(connection, user_id)
        if fail_after_stage:
            raise RuntimeError("injected account deletion failure")
        tables = _tables(connection)
        deleted += _delete_indirect_rows(connection, user_id, tables)
        for table, column in _DIRECT_OWNER_TABLES:
            if table in tables:
                deleted += connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (user_id,)).rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        _restore_files(staged)
        raise
    finally:
        connection.close()

    for _, target in staged:
        target.unlink(missing_ok=True)
    deletion_id = "del_" + hmac.new(
        db._load_secret_settings().key_material.encode("ascii"),
        f"{user_id}\0{time.time_ns()}".encode(), hashlib.sha256,
    ).hexdigest()[:24]
    return {
        "schema_version": "account-deletion.v1",
        "status": "deleted",
        "deletion_id": deletion_id,
        "deleted_rows": deleted,
    }


def enforce_retention(*, now: int | None = None) -> dict[str, int]:
    """Purge only expired authentication/control rows under documented policy."""
    current = int(time.time() if now is None else now)
    session_cutoff = current - 30 * 24 * 3600
    challenge_cutoff = current - 24 * 3600
    db.init_db()
    with connect(db.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        sessions = connection.execute(
            "DELETE FROM gateway_sessions WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)",
            (session_cutoff, session_cutoff),
        ).rowcount
        challenges = connection.execute(
            "DELETE FROM provider_auth_challenges WHERE expires_at < ?",
            (challenge_cutoff,),
        ).rowcount
        tables = _tables(connection)
        oauth_transactions = (
            connection.execute(
                "DELETE FROM oauth_transactions WHERE expires_at < ?", (challenge_cutoff,),
            ).rowcount
            if "oauth_transactions" in tables else 0
        )
        refresh = connection.execute(
            """DELETE FROM provider_refresh_tokens
               WHERE expires_at < ? OR family_id IN
                 (SELECT family_id FROM provider_session_families
                  WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?))""",
            (session_cutoff, session_cutoff, session_cutoff),
        ).rowcount
        families = connection.execute(
            """DELETE FROM provider_session_families
               WHERE (expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?))
                 AND NOT EXISTS (SELECT 1 FROM provider_refresh_tokens r WHERE r.family_id = provider_session_families.family_id)""",
            (session_cutoff, session_cutoff),
        ).rowcount
    return {
        "sessions": sessions, "challenges": challenges,
        "oauth_transactions": oauth_transactions,
        "refresh_tokens": refresh, "session_families": families,
    }
