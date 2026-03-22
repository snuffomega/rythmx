-- 015_release_extra_fields.sql
-- Add explicit, label, and per-source genre columns to lib_releases.
-- release_date and track_count already exist; these are new catalog metadata.

ALTER TABLE lib_releases ADD COLUMN explicit INTEGER DEFAULT 0;
ALTER TABLE lib_releases ADD COLUMN label TEXT;
ALTER TABLE lib_releases ADD COLUMN genre_itunes TEXT;
ALTER TABLE lib_releases ADD COLUMN genre_deezer TEXT;
