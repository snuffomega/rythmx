-- In-kind storage rule: rename source-neutral column names on lib_albums
-- to include the source that populates them. Matches the pattern already
-- established on lib_releases (genre_itunes, genre_deezer, kind_deezer,
-- kind_itunes) and lib_artists (image_url_fanart, image_url_deezer).
--
-- Display/API layer aliases these back to stable names via COALESCE or AS.

ALTER TABLE lib_albums RENAME COLUMN genre TO genre_itunes;
ALTER TABLE lib_albums RENAME COLUMN release_date TO release_date_itunes;
ALTER TABLE lib_albums RENAME COLUMN record_type TO record_type_deezer;
