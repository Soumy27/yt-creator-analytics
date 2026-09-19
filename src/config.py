"""
Central configuration. Every script imports from here.

Why this exists: hardcoded credentials and paths scattered across ten scripts
is the fastest way to make a project unscalable. One place, one source of truth.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of src/
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required env var {key!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def _int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


# ---------------------------------------------------------------- paths
DATA_DIR = ROOT / "data"
THUMBS_DIR = DATA_DIR / "thumbnails"
EXPORTS_DIR = ROOT / "exports"
LOGS_DIR = ROOT / "logs"
SQL_DIR = ROOT / "sql"
STATE_DIR = DATA_DIR / "state"

for _d in (DATA_DIR, THUMBS_DIR, EXPORTS_DIR, LOGS_DIR, STATE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- youtube
def api_keys() -> list[str]:
    """
    Returns every available API key.

    Multiple keys = multiple daily quotas, because quota is per Google Cloud
    project, not per account. This is the legitimate way to scale collection:
    create N projects, get N keys, rotate when one is exhausted.
    """
    keys = [_require("YOUTUBE_API_KEY")]
    extra = os.getenv("YOUTUBE_API_KEYS_EXTRA", "").strip()
    if extra:
        keys.extend(k.strip() for k in extra.split(",") if k.strip())
    return keys


QUOTA_DAILY_BUDGET = _int("QUOTA_DAILY_BUDGET", 9000)


# ---------------------------------------------------------------- database
def db_dsn() -> str:
    return (
        f"host={os.getenv('PGHOST', 'localhost')} "
        f"port={os.getenv('PGPORT', '5432')} "
        f"dbname={_require('PGDATABASE')} "
        f"user={_require('PGUSER')} "
        f"password={_require('PGPASSWORD')}"
    )


def db_url() -> str:
    """
    SQLAlchemy-style URL, for pandas.read_sql.

    Handles both TCP hosts and Unix socket directories. A socket path such as
    '/tmp' or '/var/run/postgresql' cannot go in the URL's host position —
    the slashes break parsing — so it is passed as a ?host= query parameter
    instead. Common on Linux where Postgres defaults to socket connections.
    """
    from urllib.parse import quote_plus

    user = quote_plus(_require("PGUSER"))
    pwd = quote_plus(_require("PGPASSWORD"))
    dbname = _require("PGDATABASE")
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")

    if host.startswith("/"):
        return (
            f"postgresql+psycopg2://{user}:{pwd}@/{dbname}"
            f"?host={quote_plus(host)}&port={port}"
        )
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{dbname}"


# ---------------------------------------------------------------- collection
MAX_VIDEOS_PER_CHANNEL = _int("MAX_VIDEOS_PER_CHANNEL", 500)
BACKFILL_SINCE = os.getenv("BACKFILL_SINCE", "").strip() or None
