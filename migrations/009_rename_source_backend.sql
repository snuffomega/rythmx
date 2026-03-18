-- 009_rename_source_backend.sql
-- Rename source_backend → source_platform across lib_* tables.
-- Aligns column naming with canonical terminology: platforms are Plex/Navidrome/Jellyfin.
-- SoulSync is an enrichment API — it no longer owns lib_* rows.
-- SoulSync rows (source_backend='soulsync') are tombstoned here.
-- User must re-sync from their actual platform (Plex/Navidrome/Jellyfin) to rebuild.

ALTER TABLE lib_artists ADD COLUMN source_platform TEXT;
ALTER TABLE lib_albums  ADD COLUMN source_platform TEXT;
ALTER TABLE lib_tracks  ADD COLUMN source_platform TEXT;

UPDATE lib_artists SET source_platform = source_backend;
UPDATE lib_albums  SET source_platform = source_backend;
UPDATE lib_tracks  SET source_platform = source_backend;

-- Tombstone legacy SoulSync-owned rows.
UPDATE lib_artists SET removed_at = CURRENT_TIMESTAMP
    WHERE source_platform = 'soulsync' AND removed_at IS NULL;
UPDATE lib_albums  SET removed_at = CURRENT_TIMESTAMP
    WHERE source_platform = 'soulsync' AND removed_at IS NULL;
UPDATE lib_tracks  SET removed_at = CURRENT_TIMESTAMP
    WHERE source_platform = 'soulsync' AND removed_at IS NULL;

-- Rename the library_backend setting key to library_platform.
UPDATE settings SET key = 'library_platform' WHERE key = 'library_backend';
-- If the stored value was 'soulsync', reset to 'plex'.
UPDATE settings SET value = 'plex'
    WHERE key = 'library_platform' AND value = 'soulsync';
