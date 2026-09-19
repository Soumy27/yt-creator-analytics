"""
PostgreSQL access layer.

Deliberately thin: psycopg2 with context managers, plus helpers for the
bulk-upsert pattern this project leans on everywhere. No ORM — the whole
point of the project is to show SQL competence, and an ORM hides it.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Sequence

import psycopg2
import psycopg2.extras

from src.config import db_dsn
from src.logging_setup import get_logger

log = get_logger(__name__)


@contextmanager
def connect(autocommit: bool = False):
    """Yields a connection, always closed. Commits on clean exit."""
    conn = psycopg2.connect(db_dsn())
    conn.autocommit = autocommit
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def cursor(autocommit: bool = False, dict_rows: bool = False):
    """Yields a cursor. dict_rows=True gives dict-like rows."""
    factory = psycopg2.extras.RealDictCursor if dict_rows else None
    with connect(autocommit=autocommit) as conn:
        with conn.cursor(cursor_factory=factory) as cur:
            yield cur


def execute(sql: str, params: Sequence[Any] | None = None) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def query(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    with cursor(dict_rows=True) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Sequence[Any] | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    with cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def bulk_upsert(
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
    page_size: int = 500,
) -> int:
    """
    INSERT ... ON CONFLICT DO UPDATE, executed in pages.

    This is the workhorse. Collection scripts are re-run constantly during
    development; every write must be idempotent or you end up with duplicate
    rows and a dataset you cannot trust.

    Returns the number of rows sent.
    """
    rows = list(rows)
    if not rows:
        return 0

    collist = ", ".join(f'"{c}"' for c in columns)
    conflict = ", ".join(f'"{c}"' for c in conflict_cols)

    if update_cols is None:
        update_cols = [c for c in columns if c not in conflict_cols]

    if update_cols:
        setclause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
        action = f"DO UPDATE SET {setclause}"
    else:
        action = "DO NOTHING"

    sql = (
        f"INSERT INTO {table} ({collist}) VALUES %s "
        f"ON CONFLICT ({conflict}) {action}"
    )

    with connect() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows, page_size=page_size)

    log.info("Upserted %d rows into %s", len(rows), table)
    return len(rows)


def table_exists(name: str) -> bool:
    return bool(
        scalar(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s)",
            (name,),
        )
    )


def row_count(table: str) -> int:
    if not table_exists(table):
        return 0
    return int(scalar(f"SELECT COUNT(*) FROM {table}") or 0)
