"""Small DB-API compatibility seam for SQLite and PostgreSQL.

The application SQL intentionally stays parameterized and portable. This
adapter translates the few legacy SQLite control/introspection statements so
all gateway stores share one PostgreSQL database in multi-instance deployments.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def is_postgres() -> bool:
    return bool(os.getenv("MAGISTRATE_DATABASE_URL", "").strip())


class Row(Sequence[Any]):
    def __init__(self, values: Sequence[Any], columns: Sequence[str]):
        self._values = tuple(values)
        self._columns = tuple(columns)
        self._index = {name: index for index, name in enumerate(columns)}

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)


class _StaticCursor:
    def __init__(self, rows: list[Sequence[Any]], columns: Sequence[str] = ("value",), rowcount: int = -1):
        self._rows = [Row(row, columns) for row in rows]
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class PostgresCursor:
    def __init__(self, connection: "PostgresConnection", raw):
        self.connection = connection
        self.raw = raw
        self.rowcount = -1

    def execute(self, sql: str, parameters: Sequence[Any] = ()):
        normalized = " ".join(sql.strip().split())
        upper = normalized.upper()
        pragma = re.fullmatch(r"PRAGMA table_info\(([^)]+)\)", normalized, re.IGNORECASE)
        if pragma:
            self.raw.execute(
                """SELECT ordinal_position - 1 AS cid, column_name AS name,
                          data_type AS type, CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                          column_default AS dflt_value, 0 AS pk
                   FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = %s
                   ORDER BY ordinal_position""",
                (pragma.group(1).strip("'\""),),
            )
            return self
        if upper in {"PRAGMA FOREIGN_KEYS = ON", "PRAGMA FOREIGN_KEYS = OFF"}:
            return _StaticCursor([])
        if upper == "PRAGMA QUICK_CHECK":
            return _StaticCursor([("ok",)])
        if upper in {"BEGIN IMMEDIATE", "BEGIN EXCLUSIVE"}:
            # Match SQLite's single-writer transaction semantics across gateway
            # instances. psycopg starts the transaction before this statement;
            # the xact-scoped lock is released automatically on commit/rollback.
            self.raw.execute("SELECT pg_advisory_xact_lock(1296126538)")
            return self
        if upper == "SELECT CHANGES()":
            return _StaticCursor([(self.connection.last_rowcount,)])
        if "SQLITE_MASTER" in upper:
            name_literal = re.search(r"name\s*=\s*'([^']+)'", sql, re.IGNORECASE)
            if name_literal:
                self.raw.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
                    (name_literal.group(1),),
                )
            else:
                self.raw.execute(
                    "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema()"
                )
            return self
        translated = sql.strip().rstrip(";")
        if re.match(r"CREATE\s+TABLE", translated, re.IGNORECASE):
            # SQLite INTEGER is signed 64-bit; PostgreSQL INTEGER is only
            # 32-bit. Preserve epoch-millisecond timestamps and counters.
            translated = re.sub(r"\bINTEGER\b", "BIGINT", translated, flags=re.IGNORECASE)
            # SQLite permits a foreign key to reference a table created later.
            # PostgreSQL resolves it immediately, so defer only those forward
            # references and install them after the legacy schema is complete.
            table_match = re.match(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z0-9_]+)", translated, re.IGNORECASE)
            foreign_key_pattern = re.compile(
                r",?\s*(FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+([A-Za-z0-9_]+)\s*\([^)]*\))",
                flags=re.IGNORECASE | re.DOTALL,
            )
            for match in list(foreign_key_pattern.finditer(translated)):
                self.raw.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
                    (match.group(2),),
                )
                if self.raw.fetchone() is None:
                    if table_match:
                        self.connection.deferred_foreign_keys.append((table_match.group(1), match.group(1)))
                    translated = translated.replace(match.group(0), "", 1)
        ignore = bool(re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", translated, re.IGNORECASE))
        if ignore:
            translated = re.sub(
                r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO\s+", r"\1INSERT INTO ", translated,
                count=1, flags=re.IGNORECASE,
            ) + " ON CONFLICT DO NOTHING"
        translated = translated.replace("?", "%s")
        try:
            self.raw.execute(translated, tuple(parameters))
        except Exception as exc:
            # Existing domain code intentionally catches sqlite's portable DB
            # exception classes. Preserve that API without leaking driver text.
            module = exc.__class__.__module__
            if module.startswith("psycopg"):
                sqlstate = getattr(exc, "sqlstate", "") or ""
                if sqlstate.startswith("23"):
                    raise sqlite3.IntegrityError("database constraint failed") from exc
                raise sqlite3.OperationalError("database operation failed") from exc
            raise
        self.rowcount = self.raw.rowcount
        self.connection.last_rowcount = self.rowcount
        return self

    @property
    def _columns(self) -> list[str]:
        return [item.name for item in self.raw.description] if self.raw.description else []

    def fetchone(self):
        value = self.raw.fetchone()
        return Row(value, self._columns) if value is not None else None

    def fetchall(self):
        columns = self._columns
        return [Row(value, columns) for value in self.raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self, raw):
        self.raw = raw
        self.row_factory = None
        self.last_rowcount = 0
        self.deferred_foreign_keys: list[tuple[str, str]] = []

    def cursor(self):
        return PostgresCursor(self, self.raw.cursor())

    def execute(self, sql: str, parameters: Sequence[Any] = ()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters):
        cursor = self.cursor()
        translated = sql.replace("?", "%s")
        cursor.raw.executemany(translated, parameters)
        cursor.rowcount = cursor.raw.rowcount
        self.last_rowcount = cursor.rowcount
        return cursor

    def apply_deferred_foreign_keys(self):
        for table, clause in self.deferred_foreign_keys:
            digest = hashlib.sha256(f"{table}\0{clause}".encode()).hexdigest()[:16]
            name = f"fk_magistrate_{digest}"
            exists = self.raw.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s", (name,),
            ).fetchone()
            if not exists:
                self.raw.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" {clause}')
        self.deferred_foreign_keys.clear()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False


@contextmanager
def observation_connection(path: str):
    """Bounded read-only operations probes; never create or migrate a database."""
    if is_postgres():
        connection = connect(path, timeout=1)
        try:
            connection.execute('SET TRANSACTION READ ONLY')
            connection.execute("SET LOCAL statement_timeout = '1000ms'")
            yield connection
        finally:
            connection.rollback()
            connection.close()
    else:
        connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            yield connection
        finally:
            connection.close()


def connect(path: str | None = None, *, timeout: float = 5.0):
    database_url = os.getenv("MAGISTRATE_DATABASE_URL", "").strip()
    if not database_url:
        return sqlite3.connect(path, timeout=timeout)
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - packaging guarantees it
        raise RuntimeError("PostgreSQL support requires psycopg") from exc
    raw = psycopg.connect(database_url, connect_timeout=max(1, int(timeout)), autocommit=False)
    return PostgresConnection(raw)
