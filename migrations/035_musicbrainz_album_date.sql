-- 035_musicbrainz_album_date.sql
-- Adds MusicBrainz Release Group ID and original release date to lib_albums.
-- Both columns are populated by the Stage 3 rich_musicbrainz_album worker.
-- Migration is idempotent — ALTER TABLE is a no-op if column already exists
-- (the runner catches and ignores "duplicate column name" errors).

ALTER TABLE lib_albums ADD COLUMN musicbrainz_release_group_id TEXT;
ALTER TABLE lib_albums ADD COLUMN original_release_date_musicbrainz TEXT;
