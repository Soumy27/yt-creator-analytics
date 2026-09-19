#!/usr/bin/env python3
"""VERIFY STEP 6 — thumbnail features are real, not defaults."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import THUMBS_DIR

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
    print("\n=== VERIFY STEP 6: thumbnail features ===\n")

    n = db.row_count("video_thumb_features")
    n_vid = db.row_count("videos")
    files = len(list(THUMBS_DIR.glob("*.jpg")))
    print(f"  features={n:,}  videos={n_vid:,}  files on disk={files:,}\n")

    check("features exist", n > 0, "(run step6)")

    print("Value ranges:")
    bad = db.scalar(
        "SELECT COUNT(*) FROM video_thumb_features WHERE brightness NOT BETWEEN 0 AND 255"
    )
    check("brightness in [0,255]", bad == 0, f"({bad})")

    bad = db.scalar("SELECT COUNT(*) FROM video_thumb_features WHERE face_count < 0")
    check("face_count non-negative", bad == 0, f"({bad})")

    bad = db.scalar(
        "SELECT COUNT(*) FROM video_thumb_features "
        "WHERE largest_face_frac NOT BETWEEN 0 AND 1"
    )
    check("largest_face_frac in [0,1]", bad == 0, f"({bad})")

    bad = db.scalar(
        "SELECT COUNT(*) FROM video_thumb_features WHERE edge_density NOT BETWEEN 0 AND 1"
    )
    check("edge_density in [0,1]", bad == 0, f"({bad})")

    print("\nFeatures actually vary (not stuck at defaults):")
    v = db.query_one(
        "SELECT COUNT(DISTINCT ROUND(brightness)) AS d_bright, "
        "       COUNT(DISTINCT face_count) AS d_faces, "
        "       COUNT(DISTINCT ROUND(colourfulness)) AS d_colour, "
        "       COUNT(*) FILTER (WHERE face_count > 0) AS with_face, "
        "       COUNT(*) FILTER (WHERE has_text) AS with_text, "
        "       COUNT(*) AS total FROM video_thumb_features"
    )
    if v and v["total"]:
        warn("brightness varies", (v["d_bright"] or 0) > 10, "(all identical?)")
        warn("colourfulness varies", (v["d_colour"] or 0) > 10)
        pct_face = 100.0 * (v["with_face"] or 0) / v["total"]
        pct_text = 100.0 * (v["with_text"] or 0) / v["total"]
        print(f"       faces detected on {pct_face:.0f}% of thumbnails")
        print(f"       text detected on   {pct_text:.0f}% of thumbnails")
        warn("face detection found some faces", (v["with_face"] or 0) > 0,
             "(0% is suspicious — check the Haar cascade loaded)")
        if pct_text == 0:
            print("       NOTE 0% text may mean tesseract BINARY is missing:")
            print("            sudo apt install tesseract-ocr")

    print("\nCoverage:")
    cov = 100.0 * n / max(1, n_vid)
    print(f"       {cov:.0f}% of videos have thumbnail features")
    warn("coverage >= 50%", cov >= 50,
         "(run step6 with a larger --limit; it costs no quota)")

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
    print("\n  STEP 6 OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
