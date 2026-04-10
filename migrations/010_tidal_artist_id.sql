-- Migration 010: Add tidal_artist_id to lib_artists for Phase 2 caching
-- Purpose: Cache Tidal artist IDs to avoid repeated artist searches

ALTER TABLE lib_artists ADD COLUMN tidal_artist_id TEXT;
CREATE INDEX idx_lib_artists_tidal_artist_id ON lib_artists(tidal_artist_id);
