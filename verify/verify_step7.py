#!/usr/bin/env python3
"""
VERIFY STEP 7 — the analysis ran and its inputs are statistically adequate.

This does NOT check that you found something interesting. Null results are
legitimate findings. It checks that the data can SUPPORT the tests you ran.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import EXPORTS_DIR

MIN_SAMPLE = 30
failed, warned = [], []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        failed.append(label); print(f"  FAIL  {label}  {detail}")


def warn(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        warned.append(label); print(f"  WARN  {label}  {detail}")


def main() -> int:
    print("\n=== VERIFY STEP 7: analysis ===\n")

    print("Sample adequacy:")
    n_long = db.scalar(
        "SELECT COUNT(*) FROM v_video_facts WHERE is_short IS NOT TRUE "
        "AND view_count IS NOT NULL"
    )
    print(f"       {n_long:,} long-form videos with view data")
    check(f"at least {MIN_SAMPLE} long-form videos", (n_long or 0) >= MIN_SAMPLE,
          f"(have {n_long}; tests are meaningless below this)")

    n_ch = db.scalar("SELECT COUNT(DISTINCT channel_id) FROM v_video_facts")
    warn("at least 10 channels", (n_ch or 0) >= 10,
         f"(have {n_ch}; few channels = channel effects masquerade as content effects)")

    print("\nNormalisation inputs:")
    no_subs = db.scalar(
        "SELECT COUNT(*) FROM v_video_facts WHERE views_per_sub IS NULL "
        "AND view_count IS NOT NULL"
    )
    warn("views_per_sub computable", (no_subs or 0) == 0,
         f"({no_subs} videos lack subscriber counts and drop out of every test)")

    print("\nFeature joins:")
    for tbl, label in [("video_text_features", "text"), ("video_thumb_features", "thumbnail")]:
        joined = db.scalar(
            f"SELECT COUNT(*) FROM v_video_facts f JOIN {tbl} t USING (video_id) "
            "WHERE f.is_short IS NOT TRUE"
        )
        pct = 100.0 * (joined or 0) / max(1, n_long or 1)
        print(f"       {label:<10} joins {joined:,} rows ({pct:.0f}%)")
        warn(f"{label} features joined", pct >= 50,
             f"(only {pct:.0f}% — run step{'5' if 'text' in tbl else '6'})")

    print("\nDistribution shape (justifies the log transform):")
    d = db.query_one(
        "SELECT MIN(view_count) AS mn, MAX(view_count) AS mx, "
        "       ROUND(AVG(view_count)) AS mean, "
        "       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY view_count) AS median "
        "FROM v_video_facts WHERE view_count > 0 AND is_short IS NOT TRUE"
    )
    if d and d["median"]:
        ratio = float(d["mean"] or 0) / float(d["median"] or 1)
        print(f"       min={d['mn']:,}  median={int(d['median']):,}  "
              f"mean={int(d['mean']):,}  max={d['mx']:,}")
        print(f"       mean/median = {ratio:.2f}")
        if ratio > 1.5:
            print("       -> Right-skewed, as expected. This is WHY the analysis")
            print("          uses log10 and rank-based tests rather than t-tests.")

    print("\nFindings file:")
    fpath = EXPORTS_DIR / "findings.json"
    if fpath.exists():
        data = json.loads(fpath.read_text())
        sig = data.get("significant_findings", [])
        print(f"       {len(sig)} significant findings recorded")
        print(f"       based on {data.get('n_videos')} videos, "
              f"{data.get('n_channels')} channels")
        if not sig:
            print("       NOTE zero significant findings is a legitimate result.")
            print("            Report it honestly; do not go hunting for a slice")
            print("            that happens to clear p<0.05 (that is p-hacking).")
        for f in sig[:5]:
            print(f"         - {f.get('analysis')}/{f.get('feature', '')}: "
                  f"p={f.get('p_value', 0):.3g}")
    else:
        check("findings.json exists", False, "(run step7)")

    print("\nVelocity readiness:")
    # Must mirror the guard in step7_analysis.a4_velocity: a row only counts
    # if the latest reading is genuinely LATER than the 24h one. Counting rows
    # alone reports "ready" on day one, when both readings are the same
    # snapshot and the test would return a meaningless rho=1.00.
    vel = db.query_one(
        "SELECT COUNT(*) AS n FROM v_video_velocity "
        "WHERE views_24h IS NOT NULL AND views_latest IS NOT NULL "
        "AND is_short IS NOT TRUE AND latest_age_hours >= 168"
    )
    nvel = (vel or {}).get("n", 0) or 0
    raw = db.query_one(
        "SELECT COUNT(*) AS n FROM v_video_velocity "
        "WHERE views_24h IS NOT NULL AND views_latest IS NOT NULL "
        "AND is_short IS NOT TRUE"
    )
    nraw = (raw or {}).get("n", 0) or 0
    print(f"       {nvel} videos have a 24h reading and a latest reading >=168h old")
    if nraw > nvel:
        print(f"       ({nraw} have both readings, but the rest are too close in time)")
    if nvel < MIN_SAMPLE:
        days = db.scalar("SELECT COUNT(DISTINCT DATE(captured_at)) FROM video_snapshots")
        print(f"       Only {days} days of snapshots so far.")
        print("       The velocity analysis needs videos published DURING")
        print("       collection. Keep the cron running; this fills in over time.")
        warned.append("velocity sample thin (time, not a bug)")
    else:
        print("  PASS  velocity analysis has an adequate sample")

    print(f"\n{'='*50}")
    if failed:
        print("  FAILED:")
        for f in failed:
            print(f"    - {f}")
        return 1
    if warned:
        print("  Warnings:")
        for w in warned:
            print(f"    - {w}")
    print("\n  STEP 7 OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
