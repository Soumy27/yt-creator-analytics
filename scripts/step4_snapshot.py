#!/usr/bin/env python3
"""
STEP 4 — Daily snapshot collector.  ** RUN THIS ON A SCHEDULE, STARTING TODAY **

WHY THIS IS THE MOST URGENT SCRIPT IN THE PROJECT
--------------------------------------------------
The YouTube API returns a video's CURRENT view count. It has no history
endpoint. If you want to answer "does early traction predict the ceiling?",
the only way is to sample the same videos repeatedly and build the series
yourself.

Elapsed time is the one input you cannot buy, borrow or backfill. Two weeks
of snapshots is a usable dataset. Two days is not. Every day you delay is a
day permanently missing from the analysis.

So: get this on a cron TONIGHT, even before the analysis code exists.

SAMPLING STRATEGY
-----------------
Young videos change fast and matter most; old videos barely move. Sampling
everything equally wastes quota on stale rows. This script tiers by age:

    < 48h    -> every run        (the critical early-velocity window)
    < 14d    -> ~every 24h
    < 90d    -> ~every 3 days
    older    -> ~weekly

Run every 6 hours and the young-video curve gets 4 points a day.

Run:      python scripts/step4_snapshot.py
          python scripts/step4_snapshot.py --all        (ignore tiering)
          python scripts/step4_snapshot.py --max 5000
Verify:   python verify/verify_step4.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.logging_setup import get_logger
from src.quota import QuotaExceeded
from src.utils import chunked, safe_int
from src.youtube import YouTubeClient

log = get_logger("step4")

SNAP_COLS = ["video_id", "view_count", "like_count", "comment_count", "age_hours"]

# Age tier -> minimum hours since the last snapshot before we resample.
TIER_SQL = """
SELECT v.video_id, v.published_at, last.captured_at AS last_capture
FROM videos v
LEFT JOIN LATERAL (
    SELECT s.captured_at FROM video_snapshots s
    WHERE s.video_id = v.video_id
    ORDER BY s.captured_at DESC LIMIT 1
) last ON TRUE
WHERE
    CASE
        -- under 48h old: always resample (early velocity window)
        WHEN v.published_at > NOW() - INTERVAL '48 hours'
            THEN TRUE
        -- under 14 days: resample if last capture > 20h ago
        WHEN v.published_at > NOW() - INTERVAL '14 days'
            THEN last.captured_at IS NULL OR last.captured_at < NOW() - INTERVAL '20 hours'
        -- under 90 days: resample if last capture > 3 days ago
        WHEN v.published_at > NOW() - INTERVAL '90 days'
            THEN last.captured_at IS NULL OR last.captured_at < NOW() - INTERVAL '3 days'
        -- older: weekly
        ELSE last.captured_at IS NULL OR last.captured_at < NOW() - INTERVAL '7 days'
    END
ORDER BY v.published_at DESC
"""

ALL_SQL = "SELECT video_id, published_at, NULL::timestamptz AS last_capture FROM videos ORDER BY published_at DESC"


def start_run(script: str) -> int:
    return int(db.query_one(
        "INSERT INTO collection_runs (script) VALUES (%s) RETURNING run_id", (script,)
    )["run_id"])


def finish_run(run_id, status, rows, quota, err=None, notes=None):
    db.execute(
        "UPDATE collection_runs SET finished_at=NOW(), status=%s, rows_written=%s, "
        "quota_spent=%s, error_message=%s, notes=%s WHERE run_id=%s",
        (status, rows, quota, err, notes, run_id),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="Snapshot every video, ignoring age tiers")
    ap.add_argument("--max", type=int, default=100_000,
                    help="Cap videos per run (safety valve)")
    ap.add_argument("--refresh-channels", action="store_true",
                    help="Also re-snapshot channel subscriber counts")
    args = ap.parse_args()

    due = db.query(ALL_SQL if args.all else TIER_SQL)
    if not due:
        log.info("Nothing due for sampling. (Videos exist? %d)", db.row_count("videos"))
        return 0

    if len(due) > args.max:
        log.warning("%d videos due, capping at %d (newest first)", len(due), args.max)
        due = due[: args.max]

    pub_by_id = {r["video_id"]: r["published_at"] for r in due}
    video_ids = list(pub_by_id.keys())

    est_units = (len(video_ids) + 49) // 50
    log.info("Sampling %d videos (~%d quota units)", len(video_ids), est_units)

    yt = YouTubeClient()
    if yt.quota_remaining() < est_units:
        log.error("Insufficient quota: need ~%d, have %d. Aborting.",
                  est_units, yt.quota_remaining())
        return 2

    run_id = start_run("step4_snapshot")
    start_quota = yt.quota_remaining()
    written = 0
    missing: list[str] = []
    now = datetime.now(timezone.utc)

    try:
        for i, batch in enumerate(chunked(video_ids, 50), start=1):
            items = yt.get_videos(batch)

            returned = {it["id"] for it in items}
            gone = set(batch) - returned
            if gone:
                # Videos deleted or made private since backfill. Expected
                # attrition — record it rather than crashing.
                missing.extend(gone)

            rows = []
            for it in items:
                st = it.get("statistics", {})
                pub = pub_by_id.get(it["id"])
                if not pub:
                    continue
                age_h = (now - pub).total_seconds() / 3600.0
                rows.append((
                    it["id"],
                    safe_int(st.get("viewCount")),
                    safe_int(st.get("likeCount")),
                    safe_int(st.get("commentCount")),
                    round(age_h, 3),
                ))

            if rows:
                db.bulk_upsert("video_snapshots", SNAP_COLS, rows,
                               conflict_cols=["video_id", "captured_at"],
                               update_cols=[])
                written += len(rows)

            if i % 20 == 0:
                log.info("  %d/%d batches, %d rows, quota left %d",
                         i, (len(video_ids) + 49) // 50, written, yt.quota_remaining())

        if args.refresh_channels:
            log.info("Refreshing channel subscriber counts...")
            chans = [r["channel_id"] for r in db.query("SELECT channel_id FROM channels WHERE is_active")]
            items = yt.get_channels(chans)
            crows = [
                (c["id"],
                 safe_int(c.get("statistics", {}).get("subscriberCount")),
                 safe_int(c.get("statistics", {}).get("videoCount")),
                 safe_int(c.get("statistics", {}).get("viewCount")))
                for c in items
            ]
            if crows:
                db.bulk_upsert("channel_snapshots",
                               ["channel_id", "subscriber_count", "video_count", "view_count"],
                               crows, conflict_cols=["channel_id", "captured_at"],
                               update_cols=[])
                for cid, subs, vc, vw in crows:
                    db.execute(
                        "UPDATE channels SET subscriber_count=%s, video_count=%s, "
                        "view_count=%s, last_refreshed_at=NOW() WHERE channel_id=%s",
                        (subs, vc, vw, cid),
                    )
                log.info("  refreshed %d channels", len(crows))

    except QuotaExceeded as exc:
        log.warning("Quota exhausted: %s", exc)
        finish_run(run_id, "partial", written, start_quota - yt.quota_remaining(), str(exc))
        return 2
    except Exception as exc:
        log.exception("Failed: %s", exc)
        finish_run(run_id, "failed", written, start_quota - yt.quota_remaining(), str(exc))
        return 1

    if missing:
        log.warning("%d videos no longer available (deleted/private)", len(missing))
        # Mark them so future runs skip them and waste no quota.
        db.execute(
            "UPDATE videos SET live_broadcast='unavailable' WHERE video_id = ANY(%s)",
            (missing,),
        )

    spent = start_quota - yt.quota_remaining()
    notes = f"{len(missing)} unavailable" if missing else None
    finish_run(run_id, "ok", written, spent, notes=notes)

    log.info("")
    log.info("Snapshots written: %d", written)
    log.info("Quota spent      : %d units", spent)
    log.info("Quota remaining  : %d units", yt.quota_remaining())

    # How much history do we have? This is the number that gates the
    # velocity analysis, so surface it on every run.
    span = db.query_one(
        "SELECT MIN(captured_at) AS first, MAX(captured_at) AS last, "
        "COUNT(DISTINCT DATE(captured_at)) AS days FROM video_snapshots"
    )
    if span and span["days"]:
        log.info("History           : %d distinct days of snapshots", span["days"])
        if span["days"] < 7:
            log.info("  -> keep this cron running; velocity analysis needs ~14 days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
