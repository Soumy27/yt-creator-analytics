#!/usr/bin/env python3
"""
VERIFY STEP 3 — video backfill is sane.

Beyond "are there rows", this checks data QUALITY: orphans, impossible
durations, future publish dates, missing thumbnails, shorts separability,
and whether the quota efficiency actually came out as designed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

MIN_VIDEOS = 100
MIN_PER_CHANNEL = 5

passed, failed, warned = [], [], []


def check(label, cond, detail=""):
    if cond:
        passed.append(label); print(f"  PASS  {label}")
    else:
        failed.append(label); print(f"  FAIL  {label}  {detail}")


def warn(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        warned.append(label); print(f"  WARN  {label}  {detail}")


def main() -> int:
    print("\n=== VERIFY STEP 3: videos ===\n")

    n_videos = db.row_count("videos")
    n_snaps = db.row_count("video_snapshots")
    n_channels = db.row_count("channels")
    print(f"  videos={n_videos:,}  snapshots={n_snaps:,}  channels={n_channels}\n")

    check(f"at least {MIN_VIDEOS} videos", n_videos >= MIN_VIDEOS,
          f"(have {n_videos}; widen --limit or add channels)")
    check("every video has a baseline snapshot", n_snaps >= n_videos,
          f"(videos={n_videos} snapshots={n_snaps})")

    print("\nReferential integrity:")
    orphan_v = db.scalar(
        "SELECT COUNT(*) FROM videos v LEFT JOIN channels c USING (channel_id) "
        "WHERE c.channel_id IS NULL"
    )
    check("no orphan videos", orphan_v == 0, f"({orphan_v})")

    orphan_s = db.scalar(
        "SELECT COUNT(*) FROM video_snapshots s LEFT JOIN videos v USING (video_id) "
        "WHERE v.video_id IS NULL"
    )
    check("no orphan snapshots", orphan_s == 0, f"({orphan_s})")

    print("\nData quality:")
    bad_dur = db.scalar(
        "SELECT COUNT(*) FROM videos WHERE duration_seconds IS NOT NULL "
        "AND (duration_seconds < 0 OR duration_seconds > 86400)"
    )
    check("durations plausible", bad_dur == 0, f"({bad_dur} outside 0–24h)")

    future = db.scalar("SELECT COUNT(*) FROM videos WHERE published_at > NOW() + INTERVAL '1 day'")
    check("no future publish dates", future == 0, f"({future})")

    null_dur = db.scalar("SELECT COUNT(*) FROM videos WHERE duration_seconds IS NULL")
    warn("durations parsed", null_dur == 0,
         f"({null_dur} unparsed — check parse_duration against live/premiere items)")

    no_thumb = db.scalar("SELECT COUNT(*) FROM videos WHERE thumbnail_url IS NULL")
    warn("thumbnails present", no_thumb == 0, f"({no_thumb} missing — step5 will skip these)")

    null_short = db.scalar("SELECT COUNT(*) FROM videos WHERE is_short IS NULL")
    check("is_short computed", null_short == 0, f"({null_short} null)")

    bad_hour = db.scalar(
        "SELECT COUNT(*) FROM videos WHERE publish_hour_utc NOT BETWEEN 0 AND 23"
    )
    check("publish_hour_utc in range", bad_hour == 0, f"({bad_hour})")

    bad_dow = db.scalar("SELECT COUNT(*) FROM videos WHERE publish_dow NOT BETWEEN 0 AND 6")
    check("publish_dow in range", bad_dow == 0, f"({bad_dow})")

    print("\nShorts separability:")
    mix = db.query_one(
        "SELECT COUNT(*) FILTER (WHERE is_short) AS shorts, "
        "       COUNT(*) FILTER (WHERE NOT is_short) AS longform FROM videos"
    )
    if mix:
        print(f"       shorts={mix['shorts']:,}  long-form={mix['longform']:,}")
        check("has long-form videos", (mix["longform"] or 0) > 0,
              "(analysis targets long-form; an all-shorts dataset needs different framing)")

    print("\nPer-channel coverage:")
    thin = db.query(
        "SELECT c.title, COUNT(v.video_id) AS n FROM channels c "
        "LEFT JOIN videos v ON v.channel_id = c.channel_id "
        "GROUP BY c.title HAVING COUNT(v.video_id) < %s ORDER BY n",
        (MIN_PER_CHANNEL,),
    )
    if thin:
        for r in thin[:10]:
            print(f"  WARN  {r['title']:<30} only {r['n']} videos")
        warned.append(f"{len(thin)} channels with <{MIN_PER_CHANNEL} videos")
    else:
        print(f"  PASS  every channel has >= {MIN_PER_CHANNEL} videos")

    print("\nSnapshot sanity:")
    neg = db.scalar("SELECT COUNT(*) FROM video_snapshots WHERE view_count < 0")
    check("no negative view counts", neg == 0, f"({neg})")
    neg_age = db.scalar("SELECT COUNT(*) FROM video_snapshots WHERE age_hours < 0")
    check("no negative ages", neg_age == 0, f"({neg_age})")

    null_likes = db.scalar("SELECT COUNT(*) FROM video_snapshots WHERE like_count IS NULL")
    if null_likes:
        print(f"  NOTE  {null_likes:,} snapshots have NULL likes "
              f"(creators can hide them — this is expected, handle with NULLIF)")

    print("\nQuota efficiency:")
    run = db.query_one(
        "SELECT rows_written, quota_spent, status FROM collection_runs "
        "WHERE script='step3_backfill_videos' ORDER BY started_at DESC LIMIT 1"
    )
    if run and run["rows_written"]:
        eff = run["quota_spent"] / run["rows_written"]
        naive = run["rows_written"] * 101
        print(f"       {run['quota_spent']}u for {run['rows_written']:,} videos "
              f"= {eff:.3f} u/video")
        print(f"       naive search.list path: ~{naive:,}u "
              f"({naive / max(run['quota_spent'],1):.0f}x more)")
        check("efficiency under 0.2 u/video", eff < 0.2,
              f"({eff:.3f} — are you accidentally using search.list?)")
        if run["status"] == "partial":
            print("  NOTE  last run hit quota and stopped early; re-run tomorrow to continue")
    else:
        warn("run recorded", False, "(no step3 run in collection_runs)")

    print(f"\n{'='*50}")
    print(f"  passed={len(passed)}  failed={len(failed)}  warnings={len(warned)}")
    if failed:
        print("\n  Fix before Step 4:")
        for f in failed:
            print(f"    - {f}")
        return 1
    if warned:
        print("\n  Warnings:")
        for w in warned:
            print(f"    - {w}")
    print("\n  STEP 3 OK. Proceed to Step 4 — and set up the cron TODAY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
