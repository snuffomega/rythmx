-- 014: Evolve lib_releases for version detection, catalog metadata, and user controls.
-- Adds normalized_title and version_type to support dedup of album variants
-- (deluxe, remaster, live, etc.) and canonical_release_id to link duplicates.
-- Catalog metadata columns (track_count, catalog_source, confidence) feed the
-- missing-album gap-analysis feature. user_dismissed lets the UI hide releases
-- the user does not care about. Best-in-class enrichment columns
-- (musicbrainz_release_group_id, original_release_date, lastfm_tags_json) are
-- nullable and backfilled by enrichment workers. A companion table
-- user_release_prefs stores per-release dismissal and priority overrides.

ALTER TABLE lib_releases ADD COLUMN normalized_title TEXT;
ALTER TABLE lib_releases ADD COLUMN version_type TEXT DEFAULT 'original';
ALTER TABLE lib_releases ADD COLUMN canonical_release_id TEXT;

ALTER TABLE lib_releases ADD COLUMN track_count INTEGER;
ALTER TABLE lib_releases ADD COLUMN catalog_source TEXT;
ALTER TABLE lib_releases ADD COLUMN confidence INTEGER DEFAULT 0;

ALTER TABLE lib_releases ADD COLUMN user_dismissed INTEGER DEFAULT 0;

ALTER TABLE lib_releases ADD COLUMN musicbrainz_release_group_id TEXT;
ALTER TABLE lib_releases ADD COLUMN original_release_date TEXT;
ALTER TABLE lib_releases ADD COLUMN lastfm_tags_json TEXT;

CREATE TABLE IF NOT EXISTS user_release_prefs (
    release_id  TEXT NOT NULL PRIMARY KEY,
    dismissed   INTEGER DEFAULT 0,
    priority    INTEGER DEFAULT 0,
    notes       TEXT,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lib_releases_canonical ON lib_releases(canonical_release_id) WHERE canonical_release_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_releases_norm_title ON lib_releases(artist_name_lower, normalized_title);
CREATE INDEX IF NOT EXISTS idx_lib_releases_missing ON lib_releases(is_owned, artist_id) WHERE is_owned = 0;
