#!/usr/bin/env python3
"""
VERIFY STEP 4 — the snapshot time series is healthy.

This verifier is different from the others: it is meant to be re-run every
few days while data accumulates. It reports HOW MUCH HISTORY you have and
whether the cron is actually firing — the two things that silently kill
this project.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

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
    print("\n=== VERIFY STEP 4: snapshot time series ===\n")

    n_snaps = db.row_count("video_snapshots")
    n_videos = db.row_count("videos")
    print(f"  snapshots={n_snaps:,}  videos={n_videos:,}")

    span = db.query_one(
        "SELECT MIN(captured_at) AS first, MAX(captured_at) AS last, "
        "       COUNT(DISTINCT DATE(captured_at)) AS days, "
        "       EXTRACT(EPOCH FROM (MAX(captured_at)-MIN(captured_at)))/86400 AS span_days "
        "FROM video_snapshots"
    )
    if not span or not span["first"]:
        print("\n  FAIL  no snapshots at all — run step4 now")
        return 1

    print(f"  first={span['first']:%Y-%m-%d %H:%M}  last={span['last']:%Y-%m-%d %H:%M}")
    print(f"  distinct days={span['days']}  span={float(span['span_days'] or 0):.1f} days\n")

    print("Collection freshness:")
    stale_h = db.scalar(
        "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(captured_at)))/3600 FROM video_snapshots"
    )
    stale_h = float(stale_h or 999)
    check("last snapshot within 36h", stale_h < 36,
          f"({stale_h:.1f}h ago — IS THE CRON RUNNING?)")

    print("\nHistory depth (gates the velocity analysis):")
    days = int(span["days"] or 0)
    if days >= 14:
        print(f"  PASS  {days} days of history — velocity analysis is viable")
        passed.append("history depth")
    elif days >= 7:
        print(f"  WARN  {days} days — usable but thin; keep collecting to 14+")
        warned.append("history depth")
    else:
        print(f"  WARN  {days} days — too thin for velocity analysis yet")
        print("        This is NOT a bug. It just needs elapsed time.")
        print("        Keep the cron running; re-check in a few days.")
        warned.append("history depth")

    print("\nMulti-snapshot coverage:")
    multi = db.query_one(
        "SELECT COUNT(*) FILTER (WHERE n = 1) AS once, "
        "       COUNT(*) FILTER (WHERE n BETWEEN 2 AND 4) AS few, "
        "       COUNT(*) FILTER (WHERE n >= 5) AS many "
        "FROM (SELECT video_id, COUNT(*) AS n FROM video_snapshots GROUP BY video_id) t"
    )
    if multi:
        print(f"       1 snapshot={multi['once']:,}  2-4={multi['few']:,}  5+={multi['many']:,}")
        warn("some videos have 3+ snapshots", (multi["many"] or 0) > 0,
             "(velocity curves need repeated sampling of the same video)")

    print("\nMonotonicity (views should never decrease):")
    # YouTube occasionally corrects view counts downward after bot filtering,
    # so a handful is normal. A large number means a collection bug.
    drops = db.scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT video_id, view_count, "
        "         LAG(view_count) OVER (PARTITION BY video_id ORDER BY captured_at) AS prev "
        "  FROM video_snapshots"
        ") t WHERE prev IS NOT NULL AND view_count < prev"
    )
    total_pairs = max(1, n_snaps - n_videos)
    drop_pct = 100.0 * (drops or 0) / total_pairs
    print(f"       {drops or 0} decreases out of ~{total_pairs:,} consecutive pairs ({drop_pct:.2f}%)")
    warn("view drops under 5%", drop_pct < 5.0,
         "(a few are normal — YouTube purges bot views — but many suggest a bug)")

    print("\nAge coverage (do we have the early window?):")
    ages = db.query_one(
        "SELECT COUNT(*) FILTER (WHERE age_hours <= 48) AS early, "
        "       COUNT(*) FILTER (WHERE age_hours BETWEEN 48 AND 240) AS week1, "
        "       COUNT(*) FILTER (WHERE age_hours > 240) AS later "
        "FROM video_snapshots"
    )
    if ages:
        print(f"       <=48h={ages['early']:,}  48-240h={ages['week1']:,}  >240h={ages['later']:,}")
        warn("has sub-48h samples", (ages["early"] or 0) > 0,
             "(the early-velocity window is the distinctive finding; "
             "you only get these by sampling videos soon after they publish)")

    print("\nVelocity view populated:")
    vel = db.query_one(
        "SELECT COUNT(*) AS n, COUNT(views_24h) AS with24, COUNT(views_168h) AS with168 "
        "FROM v_video_velocity"
    )
    if vel:
        print(f"       rows={vel['n']:,}  with 24h point={vel['with24']:,}  "
              f"with 168h point={vel['with168']:,}")
        if (vel["with24"] or 0) == 0:
            print("       (expected to be 0 until videos published DURING collection age past 24h)")

    print("\nRun history:")
    runs = db.query(
        "SELECT status, rows_written, quota_spent, started_at FROM collection_runs "
        "WHERE script='step4_snapshot' ORDER BY started_at DESC LIMIT 5"
    )
    for r in runs:
        print(f"       {r['started_at']:%Y-%m-%d %H:%M}  {r['status']:<8} "
              f"rows={r['rows_written']:<6} quota={r['quota_spent']}u")
    if runs:
        fails = sum(1 for r in runs if r["status"] == "failed")
        warn("recent runs healthy", fails == 0, f"({fails} of last {len(runs)} failed)")
    else:
        check("at least one run recorded", False, "(run step4)")

    print(f"\n{'='*50}")
    print(f"  passed={len(passed)}  failed={len(failed)}  warnings={len(warned)}")
    if failed:
        print("\n  Fix:")
        for f in failed:
            print(f"    - {f}")
        return 1
    print("\n  STEP 4 OK.")
    print("  Keep the cron running. Re-run this verifier every few days —")
    print("  it is how you notice a silently dead collector before it costs")
    print("  you a week of unrecoverable history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
