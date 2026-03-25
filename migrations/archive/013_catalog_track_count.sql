-- 013: Add track_count to lib_artist_catalog for album matching tiebreakers.
-- iTunes/Deezer return track counts per album; storing them lets the matcher
-- prefer the catalog entry whose track count is closest to the library album.

ALTER TABLE lib_artist_catalog ADD COLUMN track_count INTEGER;
