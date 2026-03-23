-- In-kind naming: add source suffix to single-source columns on lib_artists
-- and lib_tracks where the column name doesn't identify its source.
--
-- Precedent: migration 005 comment already noted "genres_json (Spotify source)".
-- The naming inconsistency was known; this migration enforces the rule that was
-- established with image_url_fanart/image_url_deezer (migration 021) and
-- lastfm_tags_json (which already encodes its source).
--
-- Display/API layer aliases back to stable names (e.g. genres_json_spotify AS genres_json).

ALTER TABLE lib_artists RENAME COLUMN genres_json TO genres_json_spotify;
ALTER TABLE lib_artists RENAME COLUMN popularity TO popularity_spotify;
ALTER TABLE lib_artists RENAME COLUMN listener_count TO listener_count_lastfm;
ALTER TABLE lib_artists RENAME COLUMN global_play_count TO play_count_lastfm;

ALTER TABLE lib_tracks RENAME COLUMN tempo TO tempo_deezer;
