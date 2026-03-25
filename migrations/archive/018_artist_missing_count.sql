-- 018_artist_missing_count.sql
-- Add precomputed missing release count to lib_artists for efficient list queries.

ALTER TABLE lib_artists ADD COLUMN missing_count INTEGER DEFAULT 0;
