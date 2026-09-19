#!/usr/bin/env python3
"""VERIFY STEP 5 — text features computed and plausible."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

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
    print("\n=== VERIFY STEP 5: text features ===\n")

    n_feat = db.row_count("video_text_features")
    n_vid = db.row_count("videos")
    print(f"  features={n_feat:,} / videos={n_vid:,}\n")

    check("features exist", n_feat > 0, "(run step5)")
    cov = 100.0 * n_feat / max(1, n_vid)
    warn(f"coverage >= 95% (have {cov:.0f}%)", cov >= 95,
         "(run: python scripts/step5_text_features.py)")

    print("\nValue ranges:")
    bad = db.scalar("SELECT COUNT(*) FROM video_text_features WHERE title_length < 0")
    check("title_length non-negative", bad == 0, f"({bad})")

    bad = db.scalar(
        "SELECT COUNT(*) FROM video_text_features WHERE caps_ratio NOT BETWEEN 0 AND 1"
    )
    check("caps_ratio in [0,1]", bad == 0, f"({bad})")

    bad = db.scalar(
        "SELECT COUNT(*) FROM video_text_features "
        "WHERE sentiment_compound IS NOT NULL AND sentiment_compound NOT BETWEEN -1 AND 1"
    )
    check("sentiment in [-1,1]", bad == 0, f"({bad})")

    mismatch = db.scalar(
        "SELECT COUNT(*) FROM video_text_features f JOIN videos v USING (video_id) "
        "WHERE f.title_length <> LENGTH(v.title)"
    )
    check("title_length matches actual title", mismatch == 0, f"({mismatch} mismatched)")

    print("\nVariance (a constant feature tells you nothing):")
    stats = db.query_one(
        "SELECT COUNT(DISTINCT title_length) AS d_len, "
        "       COUNT(DISTINCT has_number) AS d_num, "
        "       COUNT(DISTINCT caps_word_count) AS d_caps, "
        "       COUNT(*) FILTER (WHERE sentiment_compound IS NOT NULL) AS n_sent "
        "FROM video_text_features"
    )
    if stats:
        warn("title_length varies", (stats["d_len"] or 0) > 5, "(suspiciously uniform)")
        warn("has_number varies", (stats["d_num"] or 0) > 1,
             "(all-same: the feature cannot be tested)")
        warn("sentiment populated", (stats["n_sent"] or 0) > 0,
             "(install vaderSentiment)")

    print("\nDistribution sample:")
    d = db.query_one(
        "SELECT ROUND(AVG(title_length)) AS avg_len, "
        "       ROUND(100.0*AVG(CASE WHEN has_number THEN 1 ELSE 0 END)) AS pct_num, "
        "       ROUND(100.0*AVG(CASE WHEN has_question THEN 1 ELSE 0 END)) AS pct_q, "
        "       ROUND(AVG(tag_count),1) AS avg_tags FROM video_text_features"
    )
    if d:
        print(f"       avg title length={d['avg_len']}  with numbers={d['pct_num']}%  "
              f"questions={d['pct_q']}%  avg tags={d['avg_tags']}")

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
    print("\n  STEP 5 OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
