"""Durable Expo ticket/receipt accounting, separate from viewing Attention.

Only opaque ticket IDs and token hashes are retained here, never push copy or
credentials. This store owns notification delivery, never execution lifecycle.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time

from app.persistence import connect

RECEIPT_DELAY = 15 * 60
RECEIPT_TTL = 24 * 60 * 60
MAX_SENDS = 3


def install_schema(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS notification_push_deliveries (
        delivery_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        token_sha256 TEXT NOT NULL,
        ticket_id TEXT UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('sending','retry','pending','delivered','failed','expired')),
        send_attempts INTEGER NOT NULL DEFAULT 1,
        poll_attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        error_code TEXT,
        UNIQUE(user_id,item_id,fingerprint,token_sha256)
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_push_receipts_due ON notification_push_deliveries(state,next_attempt_at)")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PushDeliveryStore:
    def __init__(self, database_path: str):
        self.database_path = database_path

    def _connect(self):
        connection = connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def claim_send(self, user: str, item: str, fingerprint: str, token: str) -> dict | None:
        now = int(time.time())
        token_hash = token_digest(token)
        identity = hashlib.sha256(f"push-v1\0{user}\0{item}\0{fingerprint}\0{token_hash}".encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM notification_push_deliveries WHERE delivery_id=?", (identity,)).fetchone()
            if row:
                if row['state'] not in {'sending', 'retry'} or row['next_attempt_at'] > now:
                    return None
                if row['send_attempts'] >= MAX_SENDS:
                    connection.execute("UPDATE notification_push_deliveries SET state='failed',error_code='send_exhausted',updated_at=? WHERE delivery_id=?", (now, identity))
                    return None
                connection.execute("""UPDATE notification_push_deliveries
                    SET state='sending',send_attempts=send_attempts+1,next_attempt_at=?,updated_at=?
                    WHERE delivery_id=?""", (now + 120, now, identity))
            else:
                connection.execute("""INSERT INTO notification_push_deliveries
                    (delivery_id,user_id,item_id,fingerprint,token_sha256,state,next_attempt_at,expires_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,'sending',?,?,?,?)""",
                    (identity, user, item, fingerprint, token_hash, now + 120, now + RECEIPT_TTL, now, now))
            return dict(connection.execute("SELECT * FROM notification_push_deliveries WHERE delivery_id=?", (identity,)).fetchone())

    def accepted(self, claim: dict, ticket: str) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("""UPDATE notification_push_deliveries
                SET state='pending',ticket_id=?,next_attempt_at=?,updated_at=?,error_code=NULL
                WHERE delivery_id=? AND state='sending' AND send_attempts=?""",
                (ticket, now + RECEIPT_DELAY, now, claim['delivery_id'], claim['send_attempts']))

    def send_failed(self, claim: dict, *, retryable: bool) -> None:
        now = int(time.time())
        state = 'retry' if retryable and claim['send_attempts'] < MAX_SENDS else 'failed'
        with self._connect() as connection:
            connection.execute("""UPDATE notification_push_deliveries
                SET state=?,next_attempt_at=?,updated_at=?,error_code='send_failed'
                WHERE delivery_id=? AND state='sending' AND send_attempts=?""",
                (state, now + 60 * 2 ** claim['send_attempts'], now, claim['delivery_id'], claim['send_attempts']))

    def claim_receipts(self) -> list[dict]:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("""UPDATE notification_push_deliveries SET state='expired',error_code='receipt_expired',updated_at=?
                WHERE state='pending' AND expires_at<=?""", (now, now))
            rows = connection.execute("""SELECT * FROM notification_push_deliveries
                WHERE state='pending' AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT 100""", (now,)).fetchall()
            for row in rows:
                # The due-time claim is a cross-instance lease and bounded backoff.
                delay = min(3600, 60 * 2 ** min(row['poll_attempts'], 6))
                connection.execute("""UPDATE notification_push_deliveries
                    SET next_attempt_at=?,poll_attempts=poll_attempts+1,updated_at=? WHERE delivery_id=?""",
                    (now + delay, now, row['delivery_id']))
            return [dict(row) for row in rows]

    def receipt(self, delivery: dict, *, delivered: bool, invalid_token: bool = False) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            changed = connection.execute("""UPDATE notification_push_deliveries
                SET state=?,error_code=?,updated_at=? WHERE delivery_id=? AND state='pending'""",
                ('delivered' if delivered else 'failed', None if delivered else 'receipt_rejected', now, delivery['delivery_id'])).rowcount
            if not changed:
                return
            current = connection.execute("SELECT push_token FROM push_tokens WHERE user_id=? AND revoked_at IS NULL", (delivery['user_id'],)).fetchone()
            if not current or token_digest(current[0]) != delivery['token_sha256']:
                return  # A late receipt must never revoke/ack a replacement device.
            if invalid_token:
                connection.execute("UPDATE push_tokens SET revoked_at=? WHERE user_id=? AND push_token=?", (now, delivery['user_id'], current[0]))
            if delivered:
                connection.execute("""UPDATE notification_state SET delivered=1
                    WHERE user_id=? AND item_id=? AND fingerprint=? AND active=1""",
                    (delivery['user_id'], delivery['item_id'], delivery['fingerprint']))

    def summary(self, user: str) -> dict[str, int]:
        with self._connect() as connection:
            return {row[0]: row[1] for row in connection.execute(
                "SELECT state,COUNT(*) FROM notification_push_deliveries WHERE user_id=? GROUP BY state", (user,))}
