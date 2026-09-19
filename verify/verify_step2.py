#!/usr/bin/env python3
"""
VERIFY STEP 2 — channels loaded and usable.

The critical check is uploads_playlist: without it Step 3 has to fall back
to search.list at 100 units a call, and the whole quota strategy collapses.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

MIN_CHANNELS = 5          # below this the dataset is too thin to analyse
MIN_PER_NICHE = 2

passed, failed, warned = [], [], []


def check(label, cond, detail=""):
    if cond:
        passed.append(label); print(f"  PASS  {label}")
    else:
        failed.append(label); print(f"  FAIL  {label}  {detail}")


def warn(label, cond, detail=""):
    if not cond:
        warned.append(label); print(f"  WARN  {label}  {detail}")
    else:
        print(f"  PASS  {label}")


def main() -> int:
    print("\n=== VERIFY STEP 2: channels ===\n")

    n_niches = db.row_count("niches")
    n_channels = db.row_count("channels")
    print(f"  niches={n_niches}  channels={n_channels}\n")

    check("at least one niche", n_niches > 0, "(run step2)")
    check(f"at least {MIN_CHANNELS} channels", n_channels >= MIN_CHANNELS,
          f"(have {n_channels}; add more to config/niches.yaml)")

    print("\nCritical fields:")
    no_playlist = db.scalar(
        "SELECT COUNT(*) FROM channels WHERE uploads_playlist IS NULL OR uploads_playlist=''"
    )
    check("every channel has uploads_playlist", no_playlist == 0,
          f"({no_playlist} missing — step 3 cannot run cheaply without these)")

    no_subs = db.scalar("SELECT COUNT(*) FROM channels WHERE subscriber_count IS NULL")
    warn("subscriber counts present", no_subs == 0,
         f"({no_subs} hidden — those channels cannot be size-normalised)")

    dupes = db.scalar(
        "SELECT COUNT(*) FROM (SELECT channel_id FROM channels "
        "GROUP BY channel_id HAVING COUNT(*)>1) d"
    )
    check("no duplicate channels", dupes == 0, f"({dupes} dupes)")

    print("\nPer-niche coverage:")
    rows = db.query(
        "SELECT n.name, COUNT(c.channel_id) AS n_ch, "
        "       MIN(c.subscriber_count) AS min_subs, "
        "       MAX(c.subscriber_count) AS max_subs "
        "FROM niches n LEFT JOIN channels c ON c.niche_id = n.niche_id "
        "GROUP BY n.name ORDER BY n.name"
    )
    for r in rows:
        n = r["n_ch"]
        mn, mx = r["min_subs"], r["max_subs"]
        span = f"{mn:,}–{mx:,} subs" if mn and mx else "subs unknown"
        status = "PASS" if n >= MIN_PER_NICHE else "WARN"
        if n < MIN_PER_NICHE:
            warned.append(f"niche {r['name']} thin")
        print(f"  {status}  {r['name']:<20} {n:>3} channels   {span}")

    print("\nChannel size spread (needed for variance in views-per-sub):")
    spread = db.query_one(
        "SELECT COUNT(*) FILTER (WHERE subscriber_count >= 1000000) AS large, "
        "       COUNT(*) FILTER (WHERE subscriber_count BETWEEN 100000 AND 999999) AS mid, "
        "       COUNT(*) FILTER (WHERE subscriber_count < 100000) AS small "
        "FROM channels WHERE subscriber_count IS NOT NULL"
    )
    if spread:
        print(f"       large(1M+)={spread['large']}  mid(100k-1M)={spread['mid']}  "
              f"small(<100k)={spread['small']}")
        warn("has small channels", (spread["small"] or 0) >= 2,
             "(all-large samples flatten views-per-sub variance)")

    print("\nRun log:")
    last = db.query_one(
        "SELECT status, rows_written, quota_spent, started_at FROM collection_runs "
        "WHERE script='step2_seed_channels' ORDER BY started_at DESC LIMIT 1"
    )
    if last:
        print(f"       last run: {last['status']}  rows={last['rows_written']}  "
              f"quota={last['quota_spent']}u")
        check("last run succeeded", last["status"] == "ok", f"({last['status']})")
    else:
        check("a run was recorded", False, "(no collection_runs entry)")

    print(f"\n{'='*50}")
    print(f"  passed={len(passed)}  failed={len(failed)}  warnings={len(warned)}")
    if failed:
        print("\n  Fix before Step 3:")
        for f in failed:
            print(f"    - {f}")
        return 1
    if warned:
        print("\n  Warnings (not blocking, but they weaken the analysis):")
        for w in warned:
            print(f"    - {w}")
    print("\n  STEP 2 OK. Proceed to Step 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
