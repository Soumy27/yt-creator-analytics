#!/usr/bin/env python3
"""
VERIFY STEP 1 — schema exists and is correctly shaped.

Checks every table, every view, the critical indexes, and the foreign-key
wiring. Exits non-zero on failure so you cannot proceed on a broken base.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

EXPECTED_TABLES = [
    "niches", "channels", "channel_snapshots", "videos",
    "video_snapshots", "video_text_features", "video_thumb_features",
    "collection_runs",
]

EXPECTED_VIEWS = [
    "v_video_latest", "v_video_facts", "v_video_velocity",
    "v_niche_saturation", "v_publish_timing",
]

EXPECTED_INDEXES = [
    "idx_videos_channel", "idx_videos_published", "idx_snap_captured",
    "idx_snap_age", "idx_videos_chan_pub",
]

passed, failed = [], []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(label)
        print(f"  PASS  {label}")
    else:
        failed.append(f"{label} {detail}".strip())
        print(f"  FAIL  {label}  {detail}")


def main() -> int:
    print("\n=== VERIFY STEP 1: schema ===\n")

    try:
        v = db.scalar("SELECT version()")
        print(f"  Connected: {str(v).split(',')[0]}\n")
    except Exception as exc:
        print(f"  FAIL  cannot connect: {exc}")
        return 1

    print("Tables:")
    actual_tables = {
        r["table_name"]
        for r in db.query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
    }
    for t in EXPECTED_TABLES:
        check(f"table {t}", t in actual_tables)

    print("\nViews:")
    actual_views = {
        r["table_name"]
        for r in db.query(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema='public'"
        )
    }
    for v in EXPECTED_VIEWS:
        check(f"view {v}", v in actual_views)

    print("\nIndexes:")
    actual_idx = {
        r["indexname"]
        for r in db.query("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
    }
    for i in EXPECTED_INDEXES:
        check(f"index {i}", i in actual_idx)

    print("\nKey columns:")
    snap_cols = {
        r["column_name"]
        for r in db.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='video_snapshots'"
        )
    }
    check("video_snapshots.age_hours", "age_hours" in snap_cols,
          "(needed for every velocity curve)")
    check("video_snapshots.captured_at", "captured_at" in snap_cols)

    vid_cols = {
        r["column_name"]
        for r in db.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='videos'"
        )
    }
    check("videos.is_short", "is_short" in vid_cols,
          "(shorts must be separable or correlations are contaminated)")
    check("videos.publish_hour_utc", "publish_hour_utc" in vid_cols)

    print("\nForeign keys:")
    fks = db.query(
        "SELECT tc.table_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'"
    )
    fk_pairs = {(r["table_name"], r["column_name"]) for r in fks}
    check("videos -> channels", ("videos", "channel_id") in fk_pairs)
    check("video_snapshots -> videos", ("video_snapshots", "video_id") in fk_pairs)

    print("\nViews execute without error:")
    for v in EXPECTED_VIEWS:
        try:
            db.query(f"SELECT * FROM {v} LIMIT 1")
            check(f"{v} runs", True)
        except Exception as exc:
            check(f"{v} runs", False, str(exc)[:80])

    print(f"\n{'='*50}")
    print(f"  passed: {len(passed)}   failed: {len(failed)}")
    if failed:
        print("\n  FAILURES:")
        for f in failed:
            print(f"    - {f}")
        print("\n  Fix these before Step 2. Re-run: python scripts/step1_init_db.py")
        return 1
    print("\n  STEP 1 OK. Proceed to Step 2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
