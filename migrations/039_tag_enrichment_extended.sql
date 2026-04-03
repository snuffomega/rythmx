-- 039: Extended tag enrichment columns (Phase 29b)
-- Adds embedded lyrics and file-tag genre to lib_tracks.
-- Populated by Stage 1.1 tag_enrichment worker reading ID3/Vorbis tags via mutagen.
--
-- Notes:
--  - embedded_lyrics: plain text, HTML-stripped by mutagen. NULL when tag absent.
--  - tag_genre:       first genre string from TCON (ID3) or GENRE (Vorbis).
--                     Stored as TEXT so it feeds Forge genre affinity (Phase 29d).
--  - replay_gain_track already exists (migration 033); no new column needed here.
--
-- The migration runner skips "duplicate column name" errors, so safe to re-run.

ALTER TABLE lib_tracks ADD COLUMN embedded_lyrics TEXT;
ALTER TABLE lib_tracks ADD COLUMN tag_genre TEXT;
