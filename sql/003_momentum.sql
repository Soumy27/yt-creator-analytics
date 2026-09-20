-- =====================================================================
-- Momentum: which videos are gaining views FASTEST right now.
-- =====================================================================
-- This is the one thing here that cannot be looked up. YouTube's own
-- Trending tab is public and identical for everyone; a view-growth RATE
-- requires having sampled the same videos repeatedly over time, which is
-- exactly what video_snapshots accumulates and nobody else has for these
-- channels.
--
-- Method: for each video take its newest snapshot and the newest snapshot
-- at least 12h older, then divide the view gain by the hours between them.
-- Comparing consecutive snapshots would be too noisy — the collector runs
-- every 6h and counts update irregularly, so a short window can read as a
-- flat line or a spike depending on when YouTube refreshed its own figure.
-- =====================================================================

CREATE OR REPLACE VIEW v_video_momentum AS
WITH latest AS (
    SELECT DISTINCT ON (s.video_id)
        s.video_id, s.captured_at, s.view_count, s.age_hours
    FROM video_snapshots s
    WHERE s.view_count IS NOT NULL
    ORDER BY s.video_id, s.captured_at DESC
),
prior AS (
    SELECT DISTINCT ON (s.video_id)
        s.video_id, s.captured_at, s.view_count
    FROM video_snapshots s
    JOIN latest l ON l.video_id = s.video_id
    WHERE s.view_count IS NOT NULL
      AND s.captured_at <= l.captured_at - INTERVAL '12 hours'
    ORDER BY s.video_id, s.captured_at DESC
)
SELECT
    v.video_id,
    v.title,
    v.channel_id,
    c.title            AS channel_title,
    c.subscriber_count,
    n.name             AS niche,
    v.published_at,
    v.is_short,
    l.view_count       AS views_now,
    p.view_count       AS views_before,
    l.view_count - p.view_count AS views_gained,
    EXTRACT(EPOCH FROM (l.captured_at - p.captured_at)) / 3600.0 AS window_hours,
    l.age_hours,
    -- raw speed
    (l.view_count - p.view_count)
        / NULLIF(EXTRACT(EPOCH FROM (l.captured_at - p.captured_at)) / 3600.0, 0)
        AS views_per_hour,
    -- speed normalised by channel size, so a 26k channel can out-rank a 30M
    -- one. Without this the ranking is just "big channels post videos".
    (l.view_count - p.view_count)
        / NULLIF(EXTRACT(EPOCH FROM (l.captured_at - p.captured_at)) / 3600.0, 0)
        / NULLIF(c.subscriber_count, 0)
        AS momentum_per_sub
FROM latest l
JOIN prior p   ON p.video_id = l.video_id
JOIN videos v  ON v.video_id = l.video_id
JOIN channels c ON c.channel_id = v.channel_id
LEFT JOIN niches n ON n.niche_id = c.niche_id
WHERE l.view_count >= p.view_count;   -- guard: counts can be revised downward

-- Per-niche leaderboard, long-form only, recent videos only. A two-year-old
-- video still ticking over is not "trending" in any useful sense.
CREATE OR REPLACE VIEW v_niche_momentum AS
SELECT *
FROM (
    SELECT
        m.*,
        ROW_NUMBER() OVER (PARTITION BY m.niche ORDER BY m.momentum_per_sub DESC NULLS LAST) AS rank_in_niche
    FROM v_video_momentum m
    WHERE m.is_short IS NOT TRUE
      AND m.published_at > NOW() - INTERVAL '90 days'
      AND m.views_gained > 0
) x
WHERE rank_in_niche <= 10;
