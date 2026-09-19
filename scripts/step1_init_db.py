#!/usr/bin/env python3
"""
STEP 1 — Create the database schema.

Run:      python scripts/step1_init_db.py
Verify:   python verify/verify_step1.py

Safe to re-run: every statement is CREATE TABLE IF NOT EXISTS or
CREATE OR REPLACE VIEW.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import SQL_DIR
from src.logging_setup import get_logger

log = get_logger("step1")


def run_sql_file(path: Path) -> None:
    sql = path.read_text()
    log.info("Applying %s (%d bytes)", path.name, len(sql))
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    log.info("  applied %s", path.name)


def main() -> int:
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        log.error("No .sql files found in %s", SQL_DIR)
        return 1

    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                log.info("Connected: %s", cur.fetchone()[0].split(",")[0])
    except Exception as exc:
        log.error("Cannot connect to PostgreSQL: %s", exc)
        log.error("Check PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD in .env")
        log.error("Create the database first:  createdb yt_analytics")
        return 1

    for f in files:
        run_sql_file(f)

    log.info("Schema ready.")
    log.info("Next: python verify/verify_step1.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
