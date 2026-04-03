-- sqlfluff:dialect:sqlite
-- 030: Forge similarity graph - Tier 2 (rebuildable, purge-safe)
-- 2-hop similarity cache for Discovery tab
-- See: local-notes/FORGE-PROPOSAL-V2.md Section 5 (Runner Design)

CREATE TABLE IF NOT EXISTS forge_similarity_graph (
    artist_id          TEXT NOT NULL,
    similar_name       TEXT NOT NULL,
    similar_name_lower TEXT NOT NULL,
    hop                INTEGER NOT NULL DEFAULT 1,
    score              REAL,
    source             TEXT,
    updated_at         TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, similar_name_lower)
);

CREATE INDEX IF NOT EXISTS idx_forge_similarity_artist
    ON forge_similarity_graph(artist_id);
