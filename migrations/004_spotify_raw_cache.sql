-- 004_spotify_raw_cache.sql
-- P1: Spotify enrichment layer.
--   1. spotify_raw_cache — stores full raw API responses for dev replay / API expiry survival
--   2. New columns on lib_* tables for Spotify-unique data (genres, popularity, audio features)

-- -------------------------------------------------------------------------
-- spotify_raw_cache
-- Keyed by (query_type, entity_id). INSERT OR REPLACE refreshes stale rows.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS spotify_raw_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query_type    TEXT NOT NULL,   -- 'artist' | 'audio_features' | 'appears_on'
    entity_id     TEXT NOT NULL,   -- spotify_artist_id or spotify_track_id
    entity_name   TEXT,            -- human-readable label for debugging
    raw_json      TEXT NOT NULL,   -- complete API response blob (JSON string)
    fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(query_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_spotify_raw_entity
    ON spotify_raw_cache(query_type, entity_id);

-- -------------------------------------------------------------------------
-- lib_artists: add Spotify-unique columns
-- spotify_artist_id already exists from 002 — skip it
-- -------------------------------------------------------------------------
ALTER TABLE lib_artists ADD COLUMN genres_json   TEXT;      -- JSON array e.g. '["reggae","ska"]'
ALTER TABLE lib_artists ADD COLUMN popularity    INTEGER;   -- 0-100 Spotify popularity score

-- -------------------------------------------------------------------------
-- lib_albums: appears_on flag (Spotify detects collabs not in own discography)
-- spotify_album_id already exists from 002 — skip it
-- -------------------------------------------------------------------------
ALTER TABLE lib_albums ADD COLUMN appears_on     INTEGER DEFAULT 0;  -- 1 = collab/feature

-- -------------------------------------------------------------------------
-- lib_tracks: audio features (Spotify-only)
-- spotify_track_id already exists from 002 — skip it
-- -------------------------------------------------------------------------
ALTER TABLE lib_tracks ADD COLUMN energy         REAL;   -- 0.0-1.0  intensity/activity
ALTER TABLE lib_tracks ADD COLUMN valence        REAL;   -- 0.0-1.0  musical positivity
ALTER TABLE lib_tracks ADD COLUMN danceability   REAL;   -- 0.0-1.0  suitability for dancing
ALTER TABLE lib_tracks ADD COLUMN tempo          REAL;   -- BPM
ALTER TABLE lib_tracks ADD COLUMN acousticness   REAL;   -- 0.0-1.0  acoustic confidence
