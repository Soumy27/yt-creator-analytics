-- =====================================================================
-- Analysis views. Put the heavy lifting in SQL, not pandas.
--
-- This is deliberate and it matters for how the project reads: an analyst
-- who pushes aggregation into the database looks different from one who
-- loads everything into a dataframe and calls .groupby(). Interviewers
-- notice.
-- =====================================================================

-- ------------------------------------------------------------ latest stats
-- The most recent snapshot per video. DISTINCT ON is a Postgres-specific
-- idiom that is far faster than the window-function equivalent here.
CREATE OR REPLACE VIEW v_video_latest AS
SELECT DISTINCT ON (s.video_id)
    s.video_id,
    s.captured_at,
    s.view_count,
    s.like_count,
    s.comment_count,
    s.age_hours
FROM video_snapshots s
ORDER BY s.video_id, s.captured_at DESC;

-- ------------------------------------------------------------ core fact view
-- One row per video with everything joined. This is what most analysis
-- queries and the Power BI export sit on top of.
CREATE OR REPLACE VIEW v_video_facts AS
SELECT
    v.video_id,
    v.channel_id,
    c.title            AS channel_title,
    n.name             AS niche,
    v.title,
    v.published_at,
    v.publish_hour_utc,
    v.publish_dow,
    v.duration_seconds,
    v.is_short,
    v.category_id,
    l.view_count,
    l.like_count,
    l.comment_count,
    l.captured_at      AS stats_as_of,
    EXTRACT(EPOCH FROM (NOW() - v.published_at)) / 86400.0 AS age_days,
    c.subscriber_count,
    -- THE headline metric. Views per subscriber normalises away channel
    -- size, which is the only honest way to compare a 50k channel against
    -- a 5M one. NULLIF guards against divide-by-zero on brand-new channels.
    l.view_count::NUMERIC / NULLIF(c.subscriber_count, 0) AS views_per_sub,
    -- Engagement rates. NULL-safe: creators can hide likes/comments.
    l.like_count::NUMERIC    / NULLIF(l.view_count, 0) AS like_rate,
    l.comment_count::NUMERIC / NULLIF(l.view_count, 0) AS comment_rate
FROM videos v
JOIN channels c            ON c.channel_id = v.channel_id
LEFT JOIN niches n         ON n.niche_id   = c.niche_id
LEFT JOIN v_video_latest l ON l.video_id   = v.video_id;

-- ------------------------------------------------------------ velocity
-- Early traction vs eventual ceiling. This is the project's distinctive
-- finding and it ONLY works if snapshots have been accumulating.
--
-- For each video: views at roughly 24h, 72h and 168h after publish, plus
-- the latest reading. The LATERAL joins pick the snapshot closest to each
-- target age rather than requiring an exact hit, because cron runs drift.
CREATE OR REPLACE VIEW v_video_velocity AS
SELECT
    v.video_id,
    v.channel_id,
    v.title,
    v.published_at,
    v.is_short,
    h24.view_count  AS views_24h,
    h72.view_count  AS views_72h,
    h168.view_count AS views_168h,
    l.view_count    AS views_latest,
    l.age_hours     AS latest_age_hours,
    -- Share of current views earned in the first 24h. A high ratio means a
    -- spike-and-die video; a low one means sustained discovery (the
    -- algorithm is still serving it). That distinction is the insight.
    h24.view_count::NUMERIC / NULLIF(l.view_count, 0) AS frac_views_first_24h,
    h168.view_count::NUMERIC / NULLIF(l.view_count, 0) AS frac_views_first_week
FROM videos v
LEFT JOIN v_video_latest l ON l.video_id = v.video_id
LEFT JOIN LATERAL (
    SELECT s.view_count FROM video_snapshots s
    WHERE s.video_id = v.video_id AND s.age_hours BETWEEN 12 AND 48
    ORDER BY ABS(s.age_hours - 24) LIMIT 1
) h24 ON TRUE
LEFT JOIN LATERAL (
    SELECT s.view_count FROM video_snapshots s
    WHERE s.video_id = v.video_id AND s.age_hours BETWEEN 48 AND 120
    ORDER BY ABS(s.age_hours - 72) LIMIT 1
) h72 ON TRUE
LEFT JOIN LATERAL (
    SELECT s.view_count FROM video_snapshots s
    WHERE s.video_id = v.video_id AND s.age_hours BETWEEN 120 AND 240
    ORDER BY ABS(s.age_hours - 168) LIMIT 1
) h168 ON TRUE;

-- ------------------------------------------------------------ saturation
-- Niche saturation: are views growing faster than channels entering?
-- Computed per niche per month.
CREATE OR REPLACE VIEW v_niche_saturation AS
WITH monthly AS (
    SELECT
        n.name                                AS niche,
        DATE_TRUNC('month', v.published_at)   AS month,
        COUNT(DISTINCT v.video_id)            AS videos_published,
        COUNT(DISTINCT v.channel_id)          AS active_channels,
        SUM(l.view_count)                     AS total_views,
        AVG(l.view_count)                     AS avg_views
    FROM videos v
    JOIN channels c            ON c.channel_id = v.channel_id
    JOIN niches n              ON n.niche_id   = c.niche_id
    LEFT JOIN v_video_latest l ON l.video_id   = v.video_id
    WHERE v.is_short IS NOT TRUE
    GROUP BY 1, 2
)
SELECT
    niche,
    month,
    videos_published,
    active_channels,
    total_views,
    avg_views,
    -- Supply-demand ratio. Falling avg_views per video while channel count
    -- rises = the niche is saturating.
    total_views::NUMERIC / NULLIF(videos_published, 0) AS views_per_video,
    LAG(avg_views) OVER (PARTITION BY niche ORDER BY month) AS prev_avg_views,
    (avg_views - LAG(avg_views) OVER (PARTITION BY niche ORDER BY month))
        / NULLIF(LAG(avg_views) OVER (PARTITION BY niche ORDER BY month), 0)
        AS avg_views_mom_change
FROM monthly;

-- ------------------------------------------------------------ publish timing
CREATE OR REPLACE VIEW v_publish_timing AS
SELECT
    n.name                AS niche,
    v.publish_dow,
    v.publish_hour_utc,
    COUNT(*)              AS n_videos,
    AVG(f.views_per_sub)  AS avg_views_per_sub,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.views_per_sub) AS median_views_per_sub
FROM videos v
JOIN channels c        ON c.channel_id = v.channel_id
JOIN niches n          ON n.niche_id   = c.niche_id
JOIN v_video_facts f   ON f.video_id   = v.video_id
WHERE v.is_short IS NOT TRUE
  AND f.views_per_sub IS NOT NULL
GROUP BY 1, 2, 3;
