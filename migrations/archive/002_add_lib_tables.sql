-- 002_add_lib_tables.sql
-- Phase 10: introduce lib_* tables in rythmx.db to replace the generic-named
-- plex_reader tables (artists/albums/tracks/library_meta).
-- Also adds UNIQUE index on history to prevent duplicate CC run rows.

-- -------------------------------------------------------------------------
-- lib_artists
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lib_artists (
    id                  TEXT PRIMARY KEY,   -- Plex ratingKey
    name                TEXT NOT NULL,
    name_lower          TEXT NOT NULL,      -- indexed
    itunes_artist_id    TEXT,
    deezer_id           TEXT,
    spotify_artist_id   TEXT,
    musicbrainz_id      TEXT,
    match_confidence    INTEGER DEFAULT 0,  -- 0-100
    needs_verification  INTEGER DEFAULT 0,  -- 1 if probabilistic match
    conflict_flags      TEXT,               -- JSON string of flags
    legacy_ids          TEXT,               -- JSON map of old IDs
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lib_artists_name_lower ON lib_artists(name_lower);

-- -------------------------------------------------------------------------
-- lib_albums
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lib_albums (
    id                      TEXT PRIMARY KEY,   -- Plex ratingKey
    artist_id               TEXT NOT NULL,      -- FK -> lib_artists.id
    title                   TEXT NOT NULL,      -- display title (from Plex)
    local_title             TEXT,               -- Plex title, preserved for conflict detection
    api_title               TEXT,               -- title returned by iTunes/Deezer (may differ)
    title_lower             TEXT NOT NULL,      -- indexed with artist_id
    year                    INTEGER,
    record_type             TEXT,
    thumb_url               TEXT,
    itunes_album_id         TEXT,               -- filled by Enrich stage
    deezer_id               TEXT,               -- filled by Enrich stage
    spotify_album_id        TEXT,
    musicbrainz_release_id  TEXT,
    match_confidence        INTEGER DEFAULT 0,
    needs_verification      INTEGER DEFAULT 0,
    conflict_flags          TEXT,
    legacy_ids              TEXT,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lib_albums_artist_title ON lib_albums(artist_id, title_lower);
CREATE INDEX IF NOT EXISTS idx_lib_albums_itunes       ON lib_albums(itunes_album_id);
CREATE INDEX IF NOT EXISTS idx_lib_albums_deezer       ON lib_albums(deezer_id);

-- -------------------------------------------------------------------------
-- lib_tracks
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lib_tracks (
    id              TEXT PRIMARY KEY,   -- Plex ratingKey
    album_id        TEXT NOT NULL,      -- FK -> lib_albums.id
    artist_id       TEXT NOT NULL,      -- FK -> lib_artists.id
    title           TEXT NOT NULL,
    title_lower     TEXT NOT NULL,
    track_number    INTEGER,
    duration        INTEGER,
    file_path       TEXT,
    file_size       INTEGER,            -- fingerprint for idempotent re-sync
    spotify_track_id TEXT,
    deezer_id       TEXT,
    match_confidence INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_path, file_size)        -- stable fingerprint (NULL-safe: NULLs never conflict)
);

-- -------------------------------------------------------------------------
-- lib_meta (key-value, replaces library_meta)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lib_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- -------------------------------------------------------------------------
-- history: add UNIQUE constraint to prevent duplicate rows on re-run
-- (HIGH priority bug fix — scheduler writes history on every cycle)
-- -------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_unique
    ON history(artist_name, album_name, cycle_date);

-- -------------------------------------------------------------------------
-- Retire old plex_reader generic tables.
-- Migration runner gracefully skips "no such table" on fresh installs.
-- -------------------------------------------------------------------------
DROP TABLE artists;
DROP TABLE albums;
DROP TABLE tracks;
DROP TABLE library_meta;
