-- =====================================================================
-- Creator-facing guidance: what to tag, when to post, which niche is hot.
-- =====================================================================
-- Every number here is a CORRELATION measured on collected videos, never a
-- prediction. A tag that appears alongside better performance is not a tag
-- that causes it: creators who use a given hashtag differ in a dozen other
-- ways. The views are named and shaped so that the page can only phrase it
-- that way — there is no "predicted views" column to misread.
-- =====================================================================

-- ------------------------------------------------------- hashtags & tags
-- Two different things, deliberately kept apart:
--   hashtag - the visible #word in a title or description
--   tag     - the hidden keyword field, which feeds search, not viewers
DROP VIEW IF EXISTS v_niche_keywords;
CREATE VIEW v_niche_keywords AS
-- Lift is measured WITHIN each channel, then pooled across channels.
--
-- Comparing a word's videos against the niche median does not work, because
-- performance is views-per-subscriber and channels differ enormously in size.
-- #asmrcooking scored 52x that way: 111 of its 113 uses came from two ~57k
-- channels, in a niche whose median channel has 2.42M subscribers. The number
-- was measuring the channels, not the hashtag — the same confound that made
-- one creator's branded tag look like a 38x winner.
--
-- Asking instead "when THIS channel uses this word, do ITS OWN videos do better
-- than its others?" cancels channel size completely, because both sides of the
-- ratio come from the same channel. Pooling the per-channel ratios then needs
-- agreement across independent creators before a word ranks at all.
WITH base AS (
    SELECT n.name AS niche, v.video_id, v.channel_id, v.published_at, f.views_per_sub
    FROM videos v
    JOIN channels c       ON c.channel_id = v.channel_id
    JOIN niches n         ON n.niche_id   = c.niche_id
    JOIN v_video_facts f  ON f.video_id   = v.video_id
    WHERE v.is_short IS NOT TRUE
      AND f.views_per_sub IS NOT NULL AND f.views_per_sub > 0
      AND v.published_at > NOW() - INTERVAL '365 days'
),
words AS (
    SELECT b.niche, b.video_id, b.channel_id, b.published_at, b.views_per_sub,
           'hashtag'::TEXT AS kind, lower(m[1]) AS word
    FROM base b JOIN videos v ON v.video_id = b.video_id
    CROSS JOIN LATERAL regexp_matches(
        coalesce(v.title,'') || ' ' || coalesce(v.description,''),
        '#([A-Za-z0-9_]{2,28})', 'g') AS m
    WHERE lower(m[1]) NOT IN
        ('shorts','short','ytshorts','shortsfeed','shortvideo','youtubeshorts')
    UNION ALL
    SELECT b.niche, b.video_id, b.channel_id, b.published_at, b.views_per_sub,
           'tag'::TEXT, lower(trim(tg))
    FROM base b JOIN videos v ON v.video_id = b.video_id
    CROSS JOIN LATERAL unnest(coalesce(v.tags, ARRAY[]::TEXT[])) AS tg
    WHERE length(trim(tg)) BETWEEN 2 AND 40
),
uniq AS (SELECT DISTINCT niche, kind, word, channel_id, video_id, published_at, views_per_sub FROM words),
channel_baseline AS (
    SELECT channel_id,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_sub) AS med_all,
           COUNT(*) AS n_all
    FROM base GROUP BY channel_id
),
per_channel AS (
    SELECT u.niche, u.kind, u.word, u.channel_id,
           COUNT(*) AS n_with,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY u.views_per_sub) AS med_with,
           cb.med_all, cb.n_all
    FROM uniq u
    JOIN channel_baseline cb ON cb.channel_id = u.channel_id
    GROUP BY u.niche, u.kind, u.word, u.channel_id, cb.med_all, cb.n_all
    -- the channel must have used the word a few times AND have a real body of
    -- other work to compare against
    HAVING COUNT(*) >= 3 AND cb.n_all - COUNT(*) >= 10
)
SELECT
    pc.niche,
    pc.kind,
    pc.word,
    SUM(pc.n_with)::BIGINT                     AS n_videos,
    COUNT(*)::BIGINT                           AS n_channels,
    (SELECT COUNT(*) FROM uniq u2
      WHERE u2.niche = pc.niche AND u2.kind = pc.kind AND u2.word = pc.word
        AND u2.published_at > NOW() - INTERVAL '7 days')  AS used_last_7d,
    (SELECT COUNT(*) FROM uniq u3
      WHERE u3.niche = pc.niche AND u3.kind = pc.kind AND u3.word = pc.word
        AND u3.published_at > NOW() - INTERVAL '30 days') AS used_last_30d,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pc.med_with) AS med_views_per_sub,
    -- median across channels of (this channel with the word / this channel overall)
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY pc.med_with / NULLIF(pc.med_all, 0))        AS lift_vs_niche,
    -- how many of those channels moved the same way; a word only earns a place
    -- if independent creators agree on the direction
    COUNT(*) FILTER (WHERE pc.med_with > pc.med_all)::BIGINT AS n_channels_up
FROM per_channel pc
GROUP BY pc.niche, pc.kind, pc.word
HAVING COUNT(*) >= 3;

-- ------------------------------------------------------- when to publish
DROP VIEW IF EXISTS v_niche_best_time;
CREATE VIEW v_niche_best_time AS
SELECT
    niche, publish_dow, publish_hour_utc, n_videos, median_views_per_sub,
    ROW_NUMBER() OVER (PARTITION BY niche ORDER BY median_views_per_sub DESC) AS rank_in_niche
FROM v_publish_timing
WHERE n_videos >= 15;   -- a slot needs enough videos to mean anything

-- ------------------------------------------------------- niche momentum
-- Which subject is gaining fastest right now, normalised by channel size so
-- it is not simply a ranking of which niche has the biggest channels.
DROP VIEW IF EXISTS v_niche_heat;
CREATE VIEW v_niche_heat AS
SELECT
    m.niche,
    COUNT(*)                                        AS videos_moving,
    SUM(m.views_gained)                             AS views_gained_total,
    AVG(m.views_per_hour)                           AS avg_views_per_hour,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.momentum_per_sub) AS med_momentum_per_sub
FROM v_video_momentum m
WHERE m.is_short IS NOT TRUE
  AND m.published_at > NOW() - INTERVAL '14 days'
  AND m.views_gained > 0
GROUP BY m.niche;
