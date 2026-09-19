#!/usr/bin/env python3
"""VERIFY STEP 8 — exports exist, are non-empty, and join correctly in BI."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EXPORTS_DIR

EXPECTED = {
    "dim_channels":       ["channel_id", "channel_title", "subscriber_count", "size_tier"],
    "dim_videos":         ["video_id", "channel_id", "published_at", "duration_bucket"],
    "fact_snapshots":     ["video_id", "captured_at", "view_count", "age_hours"],
    "fact_video_summary": ["video_id", "channel_id", "view_count", "views_per_sub"],
    "agg_niche_monthly":  ["niche", "month"],
    "agg_publish_timing": ["niche", "publish_dow", "publish_hour_utc"],
}

failed, warned = [], []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        failed.append(label); print(f"  FAIL  {label}  {detail}")


def read_csv(p: Path):
    with p.open(encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames or [], list(r)


def main() -> int:
    print("\n=== VERIFY STEP 8: exports ===\n")

    tables = {}
    for name, req_cols in EXPECTED.items():
        p = EXPORTS_DIR / f"{name}.csv"
        if not p.exists():
            check(f"{name}.csv exists", False, "(run step8)")
            continue

        cols, rows = read_csv(p)
        tables[name] = (cols, rows)
        size_kb = p.stat().st_size / 1024
        print(f"  {name:<22} {len(rows):>6} rows  {len(cols):>3} cols  {size_kb:>8.1f} KB")

        missing = [c for c in req_cols if c not in cols]
        check(f"  {name} has required columns", not missing, f"missing {missing}")
        if name.startswith(("dim_", "fact_")):
            check(f"  {name} non-empty", len(rows) > 0, "(0 rows)")

    if len(tables) < len(EXPECTED):
        print("\n  Some exports missing. Run: python scripts/step8_export.py")
        return 1

    print("\nReferential integrity across the star schema:")
    ch_ids = {r["channel_id"] for r in tables["dim_channels"][1]}
    vid_ids = {r["video_id"] for r in tables["dim_videos"][1]}

    orphan_v = {r["channel_id"] for r in tables["dim_videos"][1]} - ch_ids
    check("every dim_videos.channel_id exists in dim_channels",
          not orphan_v, f"({len(orphan_v)} orphans — relationship will break in BI)")

    snap_vids = {r["video_id"] for r in tables["fact_snapshots"][1]}
    orphan_s = snap_vids - vid_ids
    check("every fact_snapshots.video_id exists in dim_videos",
          not orphan_s, f"({len(orphan_s)} orphans)")

    summ_vids = {r["video_id"] for r in tables["fact_video_summary"][1]}
    orphan_f = summ_vids - vid_ids
    check("every fact_video_summary.video_id exists in dim_videos",
          not orphan_f, f"({len(orphan_f)} orphans)")

    print("\nKey uniqueness (Power BI needs unique keys on the 1 side):")
    dv = tables["dim_videos"][1]
    check("dim_videos.video_id unique", len(vid_ids) == len(dv),
          f"({len(dv) - len(vid_ids)} duplicates — breaks the relationship)")
    dc = tables["dim_channels"][1]
    check("dim_channels.channel_id unique", len(ch_ids) == len(dc),
          f"({len(dc) - len(ch_ids)} duplicates)")

    print("\nEncoding:")
    p = EXPORTS_DIR / "dim_videos.csv"
    raw = p.read_bytes()[:3]
    if raw == b"\xef\xbb\xbf":
        print("  PASS  UTF-8 BOM present (Excel will read non-ASCII titles correctly)")
    else:
        warned.append("no BOM")
        print("  WARN  no UTF-8 BOM — Excel may mangle non-English titles")

    guide = EXPORTS_DIR / "POWERBI_SETUP.md"
    check("POWERBI_SETUP.md written", guide.exists())

    print(f"\n{'='*50}")
    if failed:
        print("  FAILED:")
        for f in failed:
            print(f"    - {f}")
        return 1
    print("\n  STEP 8 OK. Load the CSVs into Power BI and build the model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
