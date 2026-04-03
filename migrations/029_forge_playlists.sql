-- sqlfluff:dialect:sqlite
-- 029: Forge playlists - Tier 3 (permanent, user intent, never auto-purged)
-- See: local-notes/FORGE-PROPOSAL-V2.md Section 7 (Persistence Framework)

CREATE TABLE IF NOT EXISTS forge_playlists (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    plex_push_at  TEXT
);

CREATE TABLE IF NOT EXISTS forge_playlist_tracks (
    playlist_id   TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    position      INTEGER NOT NULL,
    added_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_forge_playlist_tracks_playlist
    ON forge_playlist_tracks(playlist_id);
