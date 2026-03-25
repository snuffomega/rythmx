-- Drop dead legacy columns that have been superseded by per-source columns
-- (migrations 021) or were never populated by any active code.
--
-- Requires SQLite >= 3.35.0 for ALTER TABLE DROP COLUMN.
-- Python 3.11 ships with SQLite 3.39+, so this is safe for this project.

-- lib_artists.image_url: superseded by image_url_fanart + image_url_deezer (mig 021).
-- No code reads or writes image_url on lib_artists; all queries use COALESCE(image_url_fanart, image_url_deezer).
ALTER TABLE lib_artists DROP COLUMN image_url;

-- lib_artists.legacy_ids: JSON map column, never populated by any code.
ALTER TABLE lib_artists DROP COLUMN legacy_ids;

-- lib_albums.thumb_url: superseded by thumb_url_plex + thumb_url_deezer (mig 021).
-- lib_releases.thumb_url is a DIFFERENT column and is NOT dropped here.
ALTER TABLE lib_albums DROP COLUMN thumb_url;

-- lib_albums.legacy_ids: never populated by any code.
ALTER TABLE lib_albums DROP COLUMN legacy_ids;

-- lib_tracks audio feature columns: added in migration 004 for Spotify Audio Features API.
-- That API was deprecated by Spotify in November 2024. These columns have zero references
-- in any app/ code and are permanently NULL.
ALTER TABLE lib_tracks DROP COLUMN energy;
ALTER TABLE lib_tracks DROP COLUMN valence;
ALTER TABLE lib_tracks DROP COLUMN danceability;
ALTER TABLE lib_tracks DROP COLUMN acousticness;
