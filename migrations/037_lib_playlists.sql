-- 037_lib_playlists.sql
-- Platform playlist tables (Navidrome + Plex).
-- Forge-generated playlists live in forge_playlists (separate table — do not modify).

CREATE TABLE IF NOT EXISTS lib_playlists (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_platform TEXT NOT NULL,  -- 'navidrome' | 'plex'
  cover_url TEXT,
  track_count INTEGER DEFAULT 0,
  duration_ms INTEGER DEFAULT 0,
  updated_at TEXT,
  synced_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lib_playlist_tracks (
  playlist_id TEXT NOT NULL REFERENCES lib_playlists(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES lib_tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_lib_playlist_tracks_track ON lib_playlist_tracks(track_id);
