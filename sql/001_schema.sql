-- =====================================================================
-- YouTube Creator Economy Analytics — schema
-- =====================================================================
-- Design notes:
--
-- 1. STATIC vs TIME-VARYING data is split across two tables.
--    videos          -> metadata that never changes (title, duration, publish date)
--    video_snapshots -> stats sampled repeatedly (views, likes, comments)
--
--    This split is the whole project. The API returns only CURRENT view
--    counts, never history. Velocity analysis ("does early traction predict
--    the ceiling?") is only possible if YOU build the time series by
--    sampling the same videos day after day. Start collecting immediately —
--    elapsed time is the one input you cannot buy back later.
--
-- 2. Every table is idempotent-friendly (natural PKs from YouTube IDs), so
--    re-running any collector is safe.
--
-- 3. Shorts (<=60s) are flagged, not deleted. They behave completely
--    differently from long-form and must be analysed separately or every
--    correlation in the dataset is contaminated.
-- =====================================================================

-- ------------------------------------------------------------ niches
CREATE TABLE IF NOT EXISTS niches (
    niche_id     SERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------ channels
CREATE TABLE IF NOT EXISTS channels (
    channel_id        TEXT PRIMARY KEY,
    niche_id          INTEGER REFERENCES niches(niche_id) ON DELETE SET NULL,
    title             TEXT NOT NULL,
    handle            TEXT,
    description       TEXT,
    country           TEXT,
    published_at      TIMESTAMPTZ,
    uploads_playlist  TEXT NOT NULL,
    subscriber_count  BIGINT,
    video_count       INTEGER,
    view_count        BIGINT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channels_niche  ON channels(niche_id);
CREATE INDEX IF NOT EXISTS idx_channels_active ON channels(is_active) WHERE is_active;

-- Channel size is itself time-varying (subs grow). Track it so that
-- "views normalised by subscriber count" uses the subs AT THE TIME,
-- not today's number. This matters for older videos.
CREATE TABLE IF NOT EXISTS channel_snapshots (
    channel_id       TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
    captured_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    subscriber_count BIGINT,
    video_count      INTEGER,
    view_count       BIGINT,
    PRIMARY KEY (channel_id, captured_at)
);

-- ------------------------------------------------------------ videos
CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    description       TEXT,
    published_at      TIMESTAMPTZ NOT NULL,
    duration_seconds  INTEGER,
    is_short          BOOLEAN,              -- duration <= 60s
    category_id       TEXT,
    default_language  TEXT,
    tags              TEXT[],
    thumbnail_url     TEXT,
    thumbnail_path    TEXT,                 -- local file, filled by step 5
    live_broadcast    TEXT,                 -- none / live / upcoming
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- denormalised publish-time features, computed on insert for fast SQL
    publish_hour_utc  SMALLINT,
    publish_dow       SMALLINT              -- 0=Sunday .. 6=Saturday
);

CREATE INDEX IF NOT EXISTS idx_videos_channel   ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_short     ON videos(is_short);
-- Composite index for the most common analysis query shape:
-- "all long-form videos for a channel, newest first"
CREATE INDEX IF NOT EXISTS idx_videos_chan_pub  ON videos(channel_id, published_at DESC)
    WHERE is_short IS NOT TRUE;

-- ------------------------------------------------------------ snapshots
-- THE time-series table. One row per video per sampling run.
CREATE TABLE IF NOT EXISTS video_snapshots (
    video_id       TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    view_count     BIGINT,
    like_count     BIGINT,       -- NULL when the creator hides likes
    comment_count  BIGINT,       -- NULL when comments are disabled
    -- hours since publish at capture time; the x-axis of every velocity curve
    age_hours      DOUBLE PRECISION,
    PRIMARY KEY (video_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_snap_captured ON video_snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_snap_age      ON video_snapshots(video_id, age_hours);

-- ------------------------------------------------------------ features
-- Derived features live in their own tables so that re-running feature
-- extraction never risks corrupting raw collected data.
CREATE TABLE IF NOT EXISTS video_text_features (
    video_id            TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
    title_length        INTEGER,
    title_word_count    INTEGER,
    has_number          BOOLEAN,
    has_question        BOOLEAN,
    has_exclamation     BOOLEAN,
    has_brackets        BOOLEAN,     -- [Tutorial], (2026) etc.
    caps_word_count     INTEGER,     -- fully-uppercase words, e.g. "SHOCKING"
    caps_ratio          DOUBLE PRECISION,
    sentiment_compound  DOUBLE PRECISION,
    emoji_count         INTEGER,
    tag_count           INTEGER,
    description_length  INTEGER,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_thumb_features (
    video_id          TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
    width             INTEGER,
    height            INTEGER,
    brightness        DOUBLE PRECISION,   -- 0-255 mean luminance
    contrast          DOUBLE PRECISION,   -- stddev of luminance
    saturation        DOUBLE PRECISION,
    colourfulness     DOUBLE PRECISION,   -- Hasler-Susstrunk metric
    dominant_r        INTEGER,
    dominant_g        INTEGER,
    dominant_b        INTEGER,
    face_count        INTEGER,
    largest_face_frac DOUBLE PRECISION,   -- biggest face as fraction of area
    has_text          BOOLEAN,
    text_char_count   INTEGER,
    ocr_text          TEXT,
    edge_density      DOUBLE PRECISION,   -- visual busyness proxy
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------ run log
-- Every collector run records itself. Without this you cannot tell the
-- difference between "no new videos" and "the cron silently died".
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id         SERIAL PRIMARY KEY,
    script         TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running',  -- running/ok/failed
    rows_written   INTEGER DEFAULT 0,
    quota_spent    INTEGER DEFAULT 0,
    error_message  TEXT,
    notes          TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_script ON collection_runs(script, started_at DESC);
