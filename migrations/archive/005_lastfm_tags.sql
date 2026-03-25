-- Migration 005: Last.fm tag columns for genre enrichment
-- Adds lastfm_tags_json to lib_artists and lib_albums.
-- Separate from genres_json (Spotify source) — provenance preserved.
-- Album tags fall back to parent artist tags when Last.fm has no album-level data.

ALTER TABLE lib_artists ADD COLUMN lastfm_tags_json TEXT;
ALTER TABLE lib_albums  ADD COLUMN lastfm_tags_json TEXT;
