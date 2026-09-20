#!/usr/bin/env python3
"""
Drop stored API data that has gone stale.

WHY THIS EXISTS
---------------
The YouTube API Services Terms require that data retrieved from the API is
either refreshed or deleted within 30 days. For a private experiment nobody
notices; for a deployed public site it is an obligation, and the honest way to
meet it is to delete what you have stopped refreshing rather than to keep a
growing pile of numbers that quietly drift out of date.

What this does NOT delete: anything the collector is still sampling. Videos on
tracked, active channels are re-snapshotted on a tiered schedule (step4), so
they stay fresh by construction. What goes is the residue — videos whose channel
has been dropped from niches.yaml, and rows for channels that no longer resolve.

Derived features (text, thumbnails) cascade from videos, and snapshots cascade
too, so removing a video removes everything computed from it.

Run:  python scripts/retention_sweep.py
      python scripts/retention_sweep.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.logging_setup import get_logger

log = get_logger("retention")

STALE_DAYS = 30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    ap.add_argument("--days", type=int, default=STALE_DAYS,
                    help=f"Staleness threshold in days (default {STALE_DAYS})")
    args = ap.parse_args()

    # Orphans: videos whose channel is gone from the channels table entirely.
    orphan = db.scalar(
        "SELECT COUNT(*) FROM videos v "
        "WHERE NOT EXISTS (SELECT 1 FROM channels c WHERE c.channel_id = v.channel_id)"
    ) or 0

    # Stale: not snapshotted within the window. The tiered collector touches even
    # old videos weekly, so anything past 30 days is genuinely unmaintained.
    stale = db.scalar(
        "SELECT COUNT(*) FROM videos v WHERE NOT EXISTS ("
        "  SELECT 1 FROM video_snapshots s WHERE s.video_id = v.video_id "
        f"   AND s.captured_at > NOW() - INTERVAL '{args.days} days')"
    ) or 0

    total = db.row_count("videos")
    log.info("videos=%d  orphaned=%d  not refreshed in %dd=%d",
             total, orphan, args.days, stale)

    if args.dry_run:
        log.info("Dry run — nothing deleted.")
        return 0

    if orphan:
        db.execute(
            "DELETE FROM videos v WHERE NOT EXISTS "
            "(SELECT 1 FROM channels c WHERE c.channel_id = v.channel_id)"
        )
        log.info("Deleted %d orphaned videos (cascades to snapshots and features)", orphan)

    # A guard, not a policy: if the sweep would remove most of the dataset,
    # something upstream has broken (a failed collector, a bad migration) and
    # deleting is the wrong response. Surface it instead.
    if stale and total and stale / total > 0.5:
        log.error("Refusing to delete %d/%d videos (%.0f%%) — that is not staleness, "
                  "that is a broken collector. Investigate before sweeping.",
                  stale, total, 100 * stale / total)
        return 1

    if stale:
        db.execute(
            "DELETE FROM videos v WHERE NOT EXISTS ("
            "  SELECT 1 FROM video_snapshots s WHERE s.video_id = v.video_id "
            f"   AND s.captured_at > NOW() - INTERVAL '{args.days} days')"
        )
        log.info("Deleted %d videos not refreshed in %dd", stale, args.days)

    if not orphan and not stale:
        log.info("Nothing to sweep — all stored data is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
