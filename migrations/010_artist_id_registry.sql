-- 010_artist_id_registry.sql
-- Universal Artist ID Registry: permanent per-source artist IDs stored in lib_artists.
-- Adds deezer_artist_id and lastfm_mbid (iTunes and Spotify columns already exist).
-- Adds confidence column to enrichment_meta for per-result audit trail.
-- Once an artist ID is stored at confidence >= 85, future enrichment runs skip validation
-- and call the API directly with the stored ID (fast path).

-- Per-source artist ID columns
ALTER TABLE lib_artists ADD COLUMN deezer_artist_id TEXT;
ALTER TABLE lib_artists ADD COLUMN lastfm_mbid TEXT;

-- Confidence tracking on every enrichment result
ALTER TABLE enrichment_meta ADD COLUMN confidence INTEGER;

-- Fast lookup indexes for the new artist ID columns
CREATE INDEX IF NOT EXISTS idx_lib_artists_deezer_artist_id
    ON lib_artists(deezer_artist_id) WHERE deezer_artist_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lib_artists_lastfm_mbid
    ON lib_artists(lastfm_mbid) WHERE lastfm_mbid IS NOT NULL;
