-- 038: Forge builds - pre-publish Build artifacts for Builder workflow

CREATE TABLE IF NOT EXISTS forge_builds (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',   -- new_music | custom_discovery | sync | manual
    status          TEXT NOT NULL DEFAULT 'ready',    -- queued | building | ready | published | failed
    run_mode        TEXT,                             -- build | fetch (optional)
    track_list_json TEXT NOT NULL DEFAULT '[]',      -- JSON payload for build items
    summary_json    TEXT NOT NULL DEFAULT '{}',      -- JSON summary metadata
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forge_builds_created_at
    ON forge_builds(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_forge_builds_source_status
    ON forge_builds(source, status);
