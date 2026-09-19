# Power BI setup

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
