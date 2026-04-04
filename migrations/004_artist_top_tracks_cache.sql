-- Migration 004: Artist page public top-track cache (Deezer)

CREATE TABLE IF NOT EXISTS lib_artist_top_tracks_cache (
    artist_deezer_id TEXT PRIMARY KEY,
    tracks_json      TEXT NOT NULL,
    fetched_at       TEXT DEFAULT (datetime('now')),
    expires_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_lib_artist_top_tracks_cache_expires
    ON lib_artist_top_tracks_cache(expires_at);
