#!/usr/bin/env python3
"""
STEP 3 — Backfill video metadata for every tracked channel.

THE QUOTA-EFFICIENT PATH (this is the project's technical centrepiece):

    channels.uploads_playlist   (already stored by Step 2, cost 0 now)
      -> playlistItems.list     1 unit per 50 video IDs
      -> videos.list            1 unit per 50 video records

    ~2 units per 50 videos = 0.04 units/video.

The naive alternative — search.list to find videos, then one videos.list per
video — costs ~101 units/video and dies after 99 videos. Same result,
2500x the cost. Being able to explain this difference is a genuinely good
interview answer.

Resumable: already-seen video IDs are skipped, so a run interrupted by
quota exhaustion picks up where it left off tomorrow.

Run:      python scripts/step3_backfill_videos.py
          python scripts/step3_backfill_videos.py --channel UC_xxx --limit 100
Verify:   python verify/verify_step3.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import BACKFILL_SINCE, MAX_VIDEOS_PER_CHANNEL
from src.logging_setup import get_logger
from src.quota import QuotaExceeded
from src.utils import chunked, parse_duration, parse_ts, safe_int, truncate
from src.youtube import YouTubeClient

log = get_logger("step3")

VIDEO_COLS = [
    "video_id", "channel_id", "title", "description", "published_at",
    "duration_seconds", "is_short", "category_id", "default_language",
    "tags", "thumbnail_url", "live_broadcast",
    "publish_hour_utc", "publish_dow",
]

SNAP_COLS = ["video_id", "view_count", "like_count", "comment_count", "age_hours"]


def start_run(script: str) -> int:
    return int(db.query_one(
        "INSERT INTO collection_runs (script) VALUES (%s) RETURNING run_id", (script,)
    )["run_id"])


def finish_run(run_id, status, rows, quota, err=None):
    db.execute(
        "UPDATE collection_runs SET finished_at=NOW(), status=%s, rows_written=%s, "
        "quota_spent=%s, error_message=%s WHERE run_id=%s",
        (status, rows, quota, err, run_id),
    )


def best_thumbnail(sn: dict) -> str | None:
    """Pick the highest-resolution thumbnail available."""
    th = sn.get("thumbnails", {})
    for size in ("maxres", "standard", "high", "medium", "default"):
        if size in th and th[size].get("url"):
            return th[size]["url"]
    return None


def build_video_row(item: dict) -> tuple | None:
    sn = item.get("snippet", {})
    cd = item.get("contentDetails", {})
    published = parse_ts(sn.get("publishedAt"))
    if not published:
        return None

    secs = parse_duration(cd.get("duration"))
    # Shorts are <=60s. They must stay separable: their view distribution is
    # completely different and mixing them destroys every correlation.
    is_short = (secs is not None and secs <= 60)

    return (
        item["id"],
        sn.get("channelId"),
        truncate(sn.get("title", "(untitled)"), 1000),
        truncate(sn.get("description"), 10000),
        published,
        secs,
        is_short,
        sn.get("categoryId"),
        sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
        sn.get("tags") or [],
        best_thumbnail(sn),
        sn.get("liveBroadcastContent"),
        published.hour,
        int(published.strftime("%w")),   # 0=Sunday
    )


def build_snapshot_row(item: dict, published) -> tuple:
    st = item.get("statistics", {})
    age_h = (datetime.now(timezone.utc) - published).total_seconds() / 3600.0
    return (
        item["id"],
        safe_int(st.get("viewCount")),
        safe_int(st.get("likeCount")),      # NULL if creator hides likes
        safe_int(st.get("commentCount")),   # NULL if comments disabled
        round(age_h, 3),
    )


def existing_video_ids(channel_id: str) -> set[str]:
    return {
        r["video_id"]
        for r in db.query("SELECT video_id FROM videos WHERE channel_id=%s", (channel_id,))
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", help="Only this channel ID")
    ap.add_argument("--limit", type=int, default=MAX_VIDEOS_PER_CHANNEL,
                    help="Max videos per channel")
    ap.add_argument("--since", default=BACKFILL_SINCE,
                    help="Only videos published on/after YYYY-MM-DD")
    ap.add_argument("--refresh", action="store_true",
                    help="Re-fetch videos already stored (default: skip them)")
    args = ap.parse_args()

    since_dt = None
    if args.since:
        since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    sql = "SELECT channel_id, title, uploads_playlist FROM channels WHERE is_active"
    params: tuple = ()
    if args.channel:
        sql += " AND channel_id=%s"
        params = (args.channel,)
    channels = db.query(sql + " ORDER BY title", params)

    if not channels:
        log.error("No channels found. Run step2 first.")
        return 1

    log.info("Backfilling %d channels (limit %d/channel, since=%s)",
             len(channels), args.limit, args.since or "all time")

    yt = YouTubeClient()
    run_id = start_run("step3_backfill_videos")
    start_quota = yt.quota_remaining()
    total_videos = 0
    total_snaps = 0

    try:
        for ch in channels:
            cid, title, playlist = ch["channel_id"], ch["title"], ch["uploads_playlist"]
            log.info("--- %s ---", title)

            known = set() if args.refresh else existing_video_ids(cid)

            # --- enumerate the uploads playlist (1 unit per 50) ---
            new_ids: list[str] = []
            stopped_early = False
            try:
                for vid, pub in yt.iter_playlist_video_ids(playlist, max_items=args.limit):
                    # The playlist is newest-first, so once we cross the date
                    # cutoff every subsequent item is older. Stop paging.
                    if since_dt and pub:
                        pub_dt = parse_ts(pub)
                        if pub_dt and pub_dt < since_dt:
                            stopped_early = True
                            break
                    if vid not in known:
                        new_ids.append(vid)
            except QuotaExceeded:
                raise
            except Exception as exc:
                log.error("  playlist enumeration failed: %s", str(exc)[:150])
                continue

            if stopped_early:
                log.info("  reached date cutoff, stopped paging")
            if not new_ids:
                log.info("  no new videos")
                continue
            log.info("  %d new video IDs", len(new_ids))

            # --- hydrate in batches of 50 (1 unit per 50) ---
            vrows, srows = [], []
            for batch in chunked(new_ids, 50):
                items = yt.get_videos(batch)
                for item in items:
                    row = build_video_row(item)
                    if not row:
                        continue
                    vrows.append(row)
                    srows.append(build_snapshot_row(item, row[4]))

            if vrows:
                db.bulk_upsert("videos", VIDEO_COLS, vrows,
                               conflict_cols=["video_id"],
                               update_cols=[c for c in VIDEO_COLS
                                            if c not in ("video_id", "channel_id")])
                db.bulk_upsert("video_snapshots", SNAP_COLS, srows,
                               conflict_cols=["video_id", "captured_at"],
                               update_cols=[])
                total_videos += len(vrows)
                total_snaps += len(srows)
                shorts = sum(1 for r in vrows if r[6])
                log.info("  stored %d videos (%d shorts, %d long-form)",
                         len(vrows), shorts, len(vrows) - shorts)

            log.info("  quota remaining: %d", yt.quota_remaining())

    except QuotaExceeded as exc:
        log.warning("Quota exhausted mid-run: %s", exc)
        log.warning("Progress is saved. Re-run tomorrow to continue.")
        finish_run(run_id, "partial", total_videos,
                   start_quota - yt.quota_remaining(), str(exc))
        return 2
    except Exception as exc:
        log.exception("Failed: %s", exc)
        finish_run(run_id, "failed", total_videos,
                   start_quota - yt.quota_remaining(), str(exc))
        return 1

    spent = start_quota - yt.quota_remaining()
    finish_run(run_id, "ok", total_videos, spent)

    log.info("")
    log.info("Videos stored : %d", total_videos)
    log.info("Snapshots     : %d (t=0 baseline for each video)", total_snaps)
    log.info("Quota spent   : %d units", spent)
    if total_videos:
        log.info("Efficiency    : %.3f units/video", spent / total_videos)
        log.info("  (search.list path would have cost ~%d units)", total_videos * 101)
    log.info("Next: python verify/verify_step3.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
