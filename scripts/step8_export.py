#!/usr/bin/env python3
"""
STEP 8 — Export to CSV for Power BI / Tableau / Excel.

Exports a star-ish schema rather than one giant flat file:
  dim_channels, dim_videos, fact_snapshots, fact_video_summary,
  agg_niche_monthly, agg_publish_timing

Why star schema: Power BI's model view renders the relationships visually,
which is the thing a hiring manager looks at first. One wide CSV looks
like a spreadsheet; a modelled dataset looks like BI work.

Run:      python scripts/step8_export.py
          python scripts/step8_export.py --excel
Verify:   python verify/verify_step8.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.config import EXPORTS_DIR, db_url
from src.logging_setup import get_logger

log = get_logger("step8")

EXPORTS = {
    "dim_channels": """
        SELECT c.channel_id, c.title AS channel_title, n.name AS niche,
               c.country, c.subscriber_count, c.video_count, c.view_count,
               c.published_at AS channel_created,
               CASE WHEN c.subscriber_count >= 1000000 THEN 'Large (1M+)'
                    WHEN c.subscriber_count >= 100000  THEN 'Mid (100k-1M)'
                    WHEN c.subscriber_count >= 10000   THEN 'Small (10k-100k)'
                    ELSE 'Micro (<10k)' END AS size_tier
        FROM channels c LEFT JOIN niches n USING (niche_id)
    """,
    "dim_videos": """
        SELECT v.video_id, v.channel_id, v.title, v.published_at,
               DATE(v.published_at) AS publish_date,
               v.publish_hour_utc, v.publish_dow,
               CASE v.publish_dow WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday'
                    WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
                    WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday'
                    ELSE 'Saturday' END AS publish_day_name,
               v.duration_seconds, v.is_short,
               CASE WHEN v.duration_seconds IS NULL THEN 'Unknown'
                    WHEN v.duration_seconds <= 60 THEN 'Short (<=60s)'
                    WHEN v.duration_seconds <= 300 THEN 'Brief (1-5m)'
                    WHEN v.duration_seconds <= 900 THEN 'Standard (5-15m)'
                    WHEN v.duration_seconds <= 1800 THEN 'Long (15-30m)'
                    ELSE 'Extended (30m+)' END AS duration_bucket,
               v.category_id, v.thumbnail_url,
               t.title_length, t.title_word_count, t.has_number, t.has_question,
               t.has_exclamation, t.has_brackets, t.caps_word_count,
               t.sentiment_compound, t.emoji_count, t.tag_count,
               th.brightness, th.contrast, th.saturation, th.colourfulness,
               th.face_count, th.has_text, th.text_char_count, th.edge_density
        FROM videos v
        LEFT JOIN video_text_features t USING (video_id)
        LEFT JOIN video_thumb_features th USING (video_id)
    """,
    "fact_snapshots": """
        SELECT s.video_id, s.captured_at, DATE(s.captured_at) AS capture_date,
               s.view_count, s.like_count, s.comment_count, s.age_hours,
               s.age_hours / 24.0 AS age_days
        FROM video_snapshots s
    """,
    "fact_video_summary": """
        SELECT f.video_id, f.channel_id, f.niche, f.view_count, f.like_count,
               f.comment_count, f.views_per_sub, f.like_rate, f.comment_rate,
               f.age_days, f.is_short,
               vel.views_24h, vel.views_168h,
               vel.frac_views_first_24h, vel.frac_views_first_week
        FROM v_video_facts f
        LEFT JOIN v_video_velocity vel USING (video_id)
    """,
    "agg_niche_monthly": "SELECT * FROM v_niche_saturation",
    "agg_publish_timing": "SELECT * FROM v_publish_timing",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", action="store_true",
                    help="Also write a single multi-sheet .xlsx")
    ap.add_argument("--outdir", default=str(EXPORTS_DIR))
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    url = db_url()

    frames: dict[str, pd.DataFrame] = {}
    for name, sql in EXPORTS.items():
        try:
            df = pd.read_sql(sql, url)
        except Exception as exc:
            log.error("Export %s failed: %s", name, str(exc)[:150])
            continue
        path = outdir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")  # BOM so Excel reads UTF-8
        frames[name] = df
        log.info("%-22s %6d rows -> %s", name, len(df), path.name)

    if args.excel and frames:
        xl = outdir / "youtube_analytics.xlsx"
        try:
            with pd.ExcelWriter(xl, engine="openpyxl") as w:
                for name, df in frames.items():
                    # Excel cannot store timezone-aware datetimes. Everything
                    # here is UTC already, so drop the tzinfo rather than
                    # converting — the values are unchanged.
                    out = df.copy()
                    for col in out.columns:
                        if isinstance(out[col].dtype, pd.DatetimeTZDtype):
                            out[col] = out[col].dt.tz_localize(None)
                    # Excel caps sheet names at 31 chars and rows at ~1.05M
                    out.head(1_000_000).to_excel(w, sheet_name=name[:31], index=False)
            log.info("Excel workbook -> %s", xl.name)
        except ImportError:
            log.warning("openpyxl not installed; skipped .xlsx")
        except Exception as exc:
            log.error("Excel export failed (CSVs are fine): %s", str(exc)[:150])

    (outdir / "POWERBI_SETUP.md").write_text(POWERBI_GUIDE)
    log.info("")
    log.info("Exported %d tables to %s", len(frames), outdir)
    log.info("Read POWERBI_SETUP.md for the model relationships.")
    log.info("Next: python verify/verify_step8.py")
    return 0


POWERBI_GUIDE = """# Power BI setup

## 1. Load
Get Data > Text/CSV, load all six `*.csv` files from this folder.

## 2. Model relationships (Model view)
Create these, all one-to-many, single direction:

    dim_channels[channel_id]  1 --> *  dim_videos[channel_id]
    dim_videos[video_id]      1 --> *  fact_snapshots[video_id]
    dim_videos[video_id]      1 --> 1  fact_video_summary[video_id]

Mark `dim_videos[publish_date]` as a date table if you add time intelligence.

## 3. Core measures (DAX)

```dax
Total Views = SUM(fact_video_summary[view_count])

Avg Views per Sub = AVERAGE(fact_video_summary[views_per_sub])

Median Views per Sub =
MEDIANX(fact_video_summary, fact_video_summary[views_per_sub])

Video Count = COUNTROWS(dim_videos)

Long-form Count =
CALCULATE(COUNTROWS(dim_videos), dim_videos[is_short] = FALSE)

Avg Like Rate = AVERAGE(fact_video_summary[like_rate])

First 24h Share = AVERAGE(fact_video_summary[frac_views_first_24h])
```

## 4. Pages worth building

**Page 1 — Overview**
KPI cards (video count, total views, median views/sub), a channel
scatter (subscribers vs median views/sub), a niche bar chart.

**Page 2 — Title & thumbnail patterns**
Bar charts comparing median views/sub by has_number / has_question /
has_text. Scatter of title_length vs views_per_sub. Slicer by niche.

**Page 3 — Timing**
Matrix heatmap: publish_day_name (rows) x publish_hour_utc (columns),
values = median views/sub. Label it "publish hour", not "audience time" —
the API gives no viewer timezone data.

**Page 4 — Velocity**
Line chart from fact_snapshots: age_days on X, view_count on Y, one line
per video, filtered to a single channel. Scatter of views_24h vs final
views (both log scale).

## 5. Honesty note for the dashboard
Add a text box stating: "Performance = views normalised by subscriber
count. The public YouTube API does not expose click-through rate or watch
time; those require channel ownership."

That one sentence prevents the most obvious interview challenge.
"""


if __name__ == "__main__":
    raise SystemExit(main())
