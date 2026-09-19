#!/usr/bin/env python3
"""
COLLECTOR HEALTH CHECK — is the snapshot collector still alive?

Run this any time. It answers one question: is Step 4 still firing?

WHY THIS EXISTS
---------------
Everything else in this project is recoverable. Re-run a failed backfill,
recompute features, redo the analysis — nothing is lost but time.

The snapshot collector is the exception. The YouTube Data API returns a
video's CURRENT view count and offers no history endpoint, so the time
series only exists because it is sampled repeatedly. A day the collector
does not run is a day of view history that cannot be reconstructed, ever.

A dead cron is also silent. It does not error, it simply stops, and the
symptom (a velocity analysis that never gains power) looks identical to
"not enough time has passed yet". That is the failure this guards against.

Exit code is 0 when healthy, 1 when not, so it can drive an alert:

    python scripts/check_collector.py || osascript -e 'display notification "collector down"'
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.quota import QuotaTracker

LAUNCHD_LABEL = "com.soumydhiran.ytanalytics.snapshot"
INTERVAL_HOURS = 6
# Allow one missed run before complaining: a laptop asleep through a single
# window is normal and self-corrects. Two consecutive misses is a real signal.
STALE_AFTER_HOURS = INTERVAL_HOURS * 2 + 1

failed, warned = [], []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if not cond else ""))
    if not cond:
        failed.append(label)


def warn(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'WARN'}  {label}" + (f"  {detail}" if not cond else ""))
    if not cond:
        warned.append(label)


def main() -> int:
    print("\n=== COLLECTOR HEALTH ===\n")

    # ---------------------------------------------------------- scheduler
    print("Scheduler:")
    try:
        out = subprocess.run(["launchctl", "list", LAUNCHD_LABEL],
                             capture_output=True, text=True, timeout=15)
        loaded = out.returncode == 0
        check("launchd agent loaded", loaded, f"({LAUNCHD_LABEL} not found — reload the plist)")
        if loaded:
            status = None
            for line in out.stdout.splitlines():
                if "LastExitStatus" in line:
                    status = line.split("=")[-1].strip().rstrip(";")
            print(f"       last exit status: {status}")
            check("last run exited cleanly", status in (None, "0"),
                  f"(exit {status} — check logs/launchd.err.log)")
    except (subprocess.SubprocessError, OSError) as e:
        warn("launchd reachable", False, f"({e})")

    # ---------------------------------------------------------- freshness
    print("\nFreshness:")
    row = db.query_one(
        "SELECT MAX(started_at) AS last, "
        "       EXTRACT(EPOCH FROM (NOW() - MAX(started_at)))/3600.0 AS hours_ago "
        "FROM collection_runs WHERE script LIKE '%step4%' AND status = 'ok'"
    )
    if not row or row["last"] is None:
        check("collector has ever run", False, "(no successful step4 run on record)")
    else:
        hrs = float(row["hours_ago"] or 0)
        print(f"       last successful run: {row['last']:%Y-%m-%d %H:%M} ({hrs:.1f}h ago)")
        check(f"ran within {STALE_AFTER_HOURS}h", hrs <= STALE_AFTER_HOURS,
              f"({hrs:.1f}h ago — expected every {INTERVAL_HOURS}h. THE CRON IS DEAD.)")

    # ---------------------------------------------------------- history
    print("\nHistory accumulated:")
    days = db.scalar("SELECT COUNT(DISTINCT DATE(captured_at)) FROM video_snapshots") or 0
    span = db.scalar(
        "SELECT ROUND(EXTRACT(EPOCH FROM (MAX(captured_at) - MIN(captured_at)))/86400.0, 1) "
        "FROM video_snapshots"
    ) or 0
    print(f"       {days} distinct days, spanning {span} days")
    warn("14+ days for velocity analysis", days >= 14,
         f"({days} so far — velocity stays disabled until the span is real)")

    # ---------------------------------------------------------- quota
    print("\nQuota:")
    t = QuotaTracker(key_id="key0")
    print(f"       {t.spent}/{t.budget} spent, {t.remaining()} remaining")
    check("quota not exhausted", t.remaining() > 0,
          "(exhausted — the collector cannot write until midnight Pacific)")

    # ---------------------------------------------------------- verdict
    print("\n" + "=" * 50)
    if failed:
        print("  COLLECTOR UNHEALTHY:")
        for f in failed:
            print(f"    - {f}")
        print("\n  Every hour it stays down is history you cannot recover.")
        return 1
    if warned:
        print("  Healthy, with notes:")
        for w in warned:
            print(f"    - {w}")
    else:
        print("  Collector healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
