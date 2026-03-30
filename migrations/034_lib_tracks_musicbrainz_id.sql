-- 034: Add musicbrainz_id to lib_tracks
-- lib_artists and lib_albums already have this column (genesis + 033).
-- lib_tracks was missed — Navidrome sync writes musicBrainzId from file tags here.
-- The migration runner skips "duplicate column name" errors, so this is safe to
-- apply even if the column was added manually.

ALTER TABLE lib_tracks ADD COLUMN musicbrainz_id TEXT;

CREATE INDEX IF NOT EXISTS idx_lib_tracks_musicbrainz_id
    ON lib_tracks(musicbrainz_id) WHERE musicbrainz_id IS NOT NULL;
