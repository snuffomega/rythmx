-- 006_lib_schema_evolution.sql
--
-- Library architecture overhaul:
--   1. Tombstone support — soft-delete on re-sync (removed_at) instead of hard-delete.
--      CC pipeline and owned-checks must filter WHERE removed_at IS NULL.
--   2. Source tracking — which backend (plex/soulsync) populated each row.
--   3. Track-level additions — disc_number for player ordering; itunes_track_id for
--      SoulSync import (ssync.db tracks have itunes_track_id).
--   4. lib_releases — permanent release store replacing 7-day TTL cache.
--      Releases are never deleted unless explicitly pruned (is_owned=0, age>180 days).
--

-- Tombstone support
ALTER TABLE lib_artists ADD COLUMN removed_at TEXT;
ALTER TABLE lib_albums  ADD COLUMN removed_at TEXT;
ALTER TABLE lib_tracks  ADD COLUMN removed_at TEXT;

-- Source tracking (plex | soulsync)
ALTER TABLE lib_artists ADD COLUMN source_backend TEXT DEFAULT 'plex';
ALTER TABLE lib_albums  ADD COLUMN source_backend TEXT DEFAULT 'plex';
ALTER TABLE lib_tracks  ADD COLUMN source_backend TEXT DEFAULT 'plex';

-- Track-level additions for player ordering and SoulSync import
ALTER TABLE lib_tracks ADD COLUMN disc_number     INTEGER;
ALTER TABLE lib_tracks ADD COLUMN itunes_track_id TEXT;

-- Permanent release store
-- id format: '{kind}_{deezer_album_id}' or '{kind}_{itunes_album_id}'
-- UNIQUE(artist_name_lower, title_lower, kind) deduplicates across API providers.
CREATE TABLE IF NOT EXISTS lib_releases (
    id                TEXT PRIMARY KEY,
    artist_id         TEXT,                    -- FK -> lib_artists.id (nullable)
    artist_name       TEXT NOT NULL,
    artist_name_lower TEXT NOT NULL,
    title             TEXT NOT NULL,
    title_lower       TEXT NOT NULL,
    release_date      TEXT,                    -- YYYY-MM-DD
    kind              TEXT,                    -- album | single | ep
    deezer_album_id   TEXT,
    itunes_album_id   TEXT,
    spotify_album_id  TEXT,
    thumb_url         TEXT,
    first_seen_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    last_checked_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    is_owned          INTEGER DEFAULT 0,
    owned_checked_at  TEXT,
    UNIQUE(artist_name_lower, title_lower, kind)
);

CREATE INDEX IF NOT EXISTS idx_lib_releases_artist    ON lib_releases(artist_name_lower);
CREATE INDEX IF NOT EXISTS idx_lib_releases_date      ON lib_releases(release_date);
CREATE INDEX IF NOT EXISTS idx_lib_releases_artist_id ON lib_releases(artist_id);
