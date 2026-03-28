-- 031: Forge discovered artists — Tier 2 (rebuildable, TTL 30 days)
-- External artist cache: neighbors from similarity graph not in lib_artists
-- See: local-notes/FORGE-PROPOSAL-V2.md Section 5 (Runner Design)

CREATE TABLE IF NOT EXISTS forge_discovered_artists (
    deezer_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    name_lower    TEXT NOT NULL,
    image_url     TEXT,
    fans_deezer   INTEGER,
    source_artist_id TEXT,
    fetched_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forge_discovered_artists_name
    ON forge_discovered_artists(name_lower);
