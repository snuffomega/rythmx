-- Migration 003: Dedicated Custom Discovery cache + history tables

CREATE TABLE IF NOT EXISTS forge_custom_discovery_artist_cache (
    artist_name_lower TEXT PRIMARY KEY,
    artist_name       TEXT NOT NULL,
    deezer_artist_id  TEXT,
    image_url         TEXT,
    hop               INTEGER NOT NULL DEFAULT 1,
    similarity        REAL,
    source            TEXT,
    cached_at         TEXT DEFAULT (datetime('now')),
    expires_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_fcd_artist_cache_expires
    ON forge_custom_discovery_artist_cache(expires_at);

CREATE INDEX IF NOT EXISTS idx_fcd_artist_cache_deezer
    ON forge_custom_discovery_artist_cache(deezer_artist_id);

CREATE TABLE IF NOT EXISTS forge_custom_discovery_track_cache (
    deezer_track_id   TEXT PRIMARY KEY,
    deezer_artist_id  TEXT,
    artist_name       TEXT,
    artist_name_lower TEXT,
    track_title       TEXT NOT NULL,
    track_title_lower TEXT NOT NULL,
    rank_position     INTEGER,
    deezer_rank       INTEGER,
    preview_url       TEXT,
    album_title       TEXT,
    album_cover_url   TEXT,
    cached_at         TEXT DEFAULT (datetime('now')),
    expires_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_fcd_track_cache_artist
    ON forge_custom_discovery_track_cache(deezer_artist_id, rank_position);

CREATE INDEX IF NOT EXISTS idx_fcd_track_cache_expires
    ON forge_custom_discovery_track_cache(expires_at);

CREATE TABLE IF NOT EXISTS forge_custom_discovery_runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT DEFAULT (datetime('now')),
    finished_at       TEXT,
    config_json       TEXT,
    summary_json      TEXT,
    status            TEXT NOT NULL DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS idx_fcd_runs_started
    ON forge_custom_discovery_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS forge_custom_discovery_track_history (
    deezer_track_id   TEXT PRIMARY KEY,
    deezer_artist_id  TEXT,
    artist_name       TEXT,
    artist_name_lower TEXT,
    track_title       TEXT,
    first_seen_at     TEXT DEFAULT (datetime('now')),
    last_recommended_at TEXT DEFAULT (datetime('now')),
    recommended_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_fcd_track_history_artist
    ON forge_custom_discovery_track_history(artist_name_lower, last_recommended_at DESC);
