-- 032: Forge discovered releases — Tier 2 (rebuildable, TTL 7 days)
-- Recent releases from discovered artists for Top Releases surface
-- See: local-notes/FORGE-PROPOSAL-V2.md Section 5 (Runner Design)

CREATE TABLE IF NOT EXISTS forge_discovered_releases (
    id              TEXT PRIMARY KEY,
    artist_deezer_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    record_type     TEXT,
    release_date    TEXT,
    cover_url       TEXT,
    fetched_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forge_discovered_releases_artist
    ON forge_discovered_releases(artist_deezer_id);
