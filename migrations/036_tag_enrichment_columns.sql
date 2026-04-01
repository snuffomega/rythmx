-- 036: Tag enrichment columns (bitrate / codec / container)
-- Populated by Stage 1.1 tag_enrichment worker from local music files via mutagen.
-- Navidrome-only in V1 — only fills rows where source_platform = 'navidrome'.
-- All nullable — Plex/Jellyfin rows keep NULL here.
--
-- The migration runner skips "duplicate column name" errors, so each
-- ALTER TABLE is safe to retry on partial failure or out-of-band additions.

-- lib_tracks: bitrate in kbps (stored as integer, e.g. 320 for 320 kbps)
ALTER TABLE lib_tracks ADD COLUMN bitrate INTEGER;

-- lib_tracks: codec identifier (e.g. 'FLAC', 'MP3', 'AAC', 'OGG', 'OPUS')
ALTER TABLE lib_tracks ADD COLUMN codec TEXT;

-- lib_tracks: container format (e.g. 'flac', 'mp3', 'm4a', 'ogg', 'opus')
ALTER TABLE lib_tracks ADD COLUMN container TEXT;

-- Partial index on codec — used by tag_enrichment to find un-enriched tracks
CREATE INDEX IF NOT EXISTS idx_lib_tracks_codec_null
    ON lib_tracks(id) WHERE codec IS NULL;
