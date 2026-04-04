CREATE TABLE IF NOT EXISTS match_overrides (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confirmed_id TEXT,
    state TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    updated_by TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_type, entity_id, source)
);

CREATE TABLE IF NOT EXISTS match_override_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL,
    action TEXT NOT NULL,
    candidate_id TEXT,
    note TEXT,
    actor TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_match_overrides_lookup
    ON match_overrides(entity_type, source, locked);

CREATE INDEX IF NOT EXISTS idx_match_override_events_entity
    ON match_override_events(entity_type, entity_id, source, created_at);
