-- Plex Stage 1 data gaps: capture high-value fields already available from Plex
-- but not yet extracted during library sync.
--
-- Tier 1 — immediate value, low effort (direct Plex attributes).

ALTER TABLE lib_albums ADD COLUMN label_plex TEXT;
ALTER TABLE lib_albums ADD COLUMN plex_release_date TEXT;
ALTER TABLE lib_albums ADD COLUMN last_viewed_at TEXT;

ALTER TABLE lib_tracks ADD COLUMN skip_count INTEGER DEFAULT 0;
ALTER TABLE lib_tracks ADD COLUMN last_viewed_at TEXT;
ALTER TABLE lib_tracks ADD COLUMN added_at TEXT;

ALTER TABLE lib_artists ADD COLUMN added_at TEXT;
