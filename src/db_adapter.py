"""
Database adapter with sqlite3-compatible surface.

Default behavior:
- Use built-in sqlite3 when DATABASE_URL is not set.
- Use PostgreSQL (psycopg) when DATABASE_URL is set.
"""

from __future__ import annotations

import os
import re
import sqlite3 as _sqlite3
from typing import Any, Iterable, Optional

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - only needed in postgres mode
    psycopg = None
    dict_row = None


DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

# Keep sqlite-like symbol usage in service code.
Row = _sqlite3.Row


def _normalize_sql(sql: str) -> str:
    s = sql
    # SQLite placeholders -> PostgreSQL placeholders
    s = s.replace("?", "%s")
    # SQLite autoincrement syntax -> PostgreSQL serial
    s = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        s,
        flags=re.IGNORECASE,
    )
    return s


class PostgresCursorWrapper:
    def __init__(self, conn_wrapper: "PostgresConnectionWrapper", inner):
        self._conn_wrapper = conn_wrapper
        self._inner = inner
        self.lastrowid = None

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        normalized = _normalize_sql(sql)
        if params is None:
            self._inner.execute(normalized)
        else:
            self._inner.execute(normalized, params)

        # Approximate sqlite cursor.lastrowid behavior for INSERT statements.
        if normalized.lstrip().upper().startswith("INSERT"):
            try:
                self._inner.execute("SELECT LASTVAL()")
                row = self._inner.fetchone()
                self.lastrowid = row[0] if row else None
            except Exception:
                self.lastrowid = None
        return self

    def executemany(self, sql: str, seq_of_params):
        normalized = _normalize_sql(sql)
        self._inner.executemany(normalized, seq_of_params)
        return self

    def fetchall(self):
        return self._inner.fetchall()

    def fetchone(self):
        return self._inner.fetchone()

    def __iter__(self):
        return iter(self._inner)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class PostgresConnectionWrapper:
    def __init__(self, inner):
        self._inner = inner
        self.row_factory = None

    def cursor(self):
        if self.row_factory is Row and dict_row is not None:
            cur = self._inner.cursor(row_factory=dict_row)
        else:
            cur = self._inner.cursor()
        return PostgresCursorWrapper(self, cur)

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()

    def close(self):
        return self._inner.close()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def connect(path_or_dsn: str, *args, **kwargs):
    """
    sqlite3.connect-compatible entrypoint.
    - For sqlite mode, path_or_dsn is the file path.
    - For postgres mode, DATABASE_URL is used and path_or_dsn is ignored.
    """
    if not USE_POSTGRES:
        return _sqlite3.connect(path_or_dsn, *args, **kwargs)

    if psycopg is None:
        raise RuntimeError(
            "PostgreSQL mode requested but psycopg is not installed. "
            "Install dependency: psycopg[binary]"
        )

    conn = psycopg.connect(DATABASE_URL)
    return PostgresConnectionWrapper(conn)


def table_exists(conn, table_name: str) -> bool:
    """Cross-database table existence check."""
    cursor = conn.cursor()
    if USE_POSTGRES:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
            """,
            (table_name,),
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None
