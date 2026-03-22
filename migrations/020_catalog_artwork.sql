-- 020: Artwork as enrichment data.
-- Store album art URLs alongside catalog entries (already in API responses, currently dropped).
-- Store artist photo URL on lib_artists (populated by new enrichment stage).
ALTER TABLE lib_artist_catalog ADD COLUMN artwork_url TEXT;
ALTER TABLE lib_artists ADD COLUMN image_url TEXT;
