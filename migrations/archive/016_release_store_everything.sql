-- 016_release_store_everything.sql
--
-- Rebuilds lib_releases to:
--   1. Drop UNIQUE(artist_name_lower, title_lower, kind) — SQLite cannot DROP CONSTRAINT
--      directly, so we recreate the table. The constraint was introduced in migration 006
--      and was designed for single-source dedup, but multi-source ingestion now requires
--      the same (artist, title, kind) tuple to appear from both Deezer and iTunes without
--      collision. Deduplication is now handled at the query/merge layer.
--   2. Add kind_deezer TEXT and kind_itunes TEXT columns for per-source kind storage,
--      positioned immediately after the existing kind column.
--   3. id TEXT PRIMARY KEY is the only remaining constraint.
--   4. All data is preserved; the two new columns are initialised to NULL.
--   5. All indexes from migrations 006 and 014 are recreated after the rebuild.

-- Step 1: Rename existing table so we can copy its data
ALTER TABLE lib_releases RENAME TO lib_releases_old;

-- Step 2: Create the new table — all existing columns plus kind_deezer / kind_itunes,
--         NO UNIQUE constraint on (artist_name_lower, title_lower, kind)
CREATE TABLE lib_releases (
    id                              TEXT PRIMARY KEY,
    artist_id                       TEXT,
    artist_name                     TEXT NOT NULL,
    artist_name_lower               TEXT NOT NULL,
    title                           TEXT NOT NULL,
    title_lower                     TEXT NOT NULL,
    release_date                    TEXT,
    kind                            TEXT,
    kind_deezer                     TEXT,
    kind_itunes                     TEXT,
    deezer_album_id                 TEXT,
    itunes_album_id                 TEXT,
    spotify_album_id                TEXT,
    thumb_url                       TEXT,
    first_seen_at                   TEXT DEFAULT CURRENT_TIMESTAMP,
    last_checked_at                 TEXT DEFAULT CURRENT_TIMESTAMP,
    is_owned                        INTEGER DEFAULT 0,
    owned_checked_at                TEXT,
    normalized_title                TEXT,
    version_type                    TEXT DEFAULT 'original',
    canonical_release_id            TEXT,
    track_count                     INTEGER,
    catalog_source                  TEXT,
    confidence                      INTEGER DEFAULT 0,
    user_dismissed                  INTEGER DEFAULT 0,
    musicbrainz_release_group_id    TEXT,
    original_release_date           TEXT,
    lastfm_tags_json                TEXT,
    explicit                        INTEGER DEFAULT 0,
    label                           TEXT,
    genre_itunes                    TEXT,
    genre_deezer                    TEXT
);

-- Step 3: Copy all existing rows; new columns default to NULL
INSERT INTO lib_releases SELECT
    id,
    artist_id,
    artist_name,
    artist_name_lower,
    title,
    title_lower,
    release_date,
    kind,
    NULL,           -- kind_deezer (new)
    NULL,           -- kind_itunes (new)
    deezer_album_id,
    itunes_album_id,
    spotify_album_id,
    thumb_url,
    first_seen_at,
    last_checked_at,
    is_owned,
    owned_checked_at,
    normalized_title,
    version_type,
    canonical_release_id,
    track_count,
    catalog_source,
    confidence,
    user_dismissed,
    musicbrainz_release_group_id,
    original_release_date,
    lastfm_tags_json,
    explicit,
    label,
    genre_itunes,
    genre_deezer
FROM lib_releases_old;

-- Step 4: Remove the old table
DROP TABLE lib_releases_old;

-- Step 5: Recreate all indexes (from migrations 006 and 014)
CREATE INDEX IF NOT EXISTS idx_lib_releases_artist     ON lib_releases(artist_name_lower);
CREATE INDEX IF NOT EXISTS idx_lib_releases_date       ON lib_releases(release_date);
CREATE INDEX IF NOT EXISTS idx_lib_releases_artist_id  ON lib_releases(artist_id);
CREATE INDEX IF NOT EXISTS idx_lib_releases_canonical  ON lib_releases(canonical_release_id) WHERE canonical_release_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_releases_norm_title ON lib_releases(artist_name_lower, normalized_title);
CREATE INDEX IF NOT EXISTS idx_lib_releases_missing    ON lib_releases(is_owned, artist_id) WHERE is_owned = 0;
