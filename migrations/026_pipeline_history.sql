-- Migration 026: pipeline_history table
-- Captures metadata for each pipeline run (New Music / Custom Discovery).
-- Does NOT replace the per-release `history` table.

CREATE TABLE IF NOT EXISTS pipeline_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_type TEXT    NOT NULL,                      -- 'new_music' | 'custom_discovery'
    run_mode      TEXT    NOT NULL,                      -- 'preview' | 'build' | 'fetch'
    status        TEXT    NOT NULL DEFAULT 'running',    -- 'running' | 'completed' | 'error'
    config_json   TEXT,                                  -- settings snapshot at run time
    started_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   TIMESTAMP,
    run_duration  REAL,                                  -- seconds
    summary_json  TEXT,                                  -- JSON result summary
    error_message TEXT,
    triggered_by  TEXT    NOT NULL DEFAULT 'manual'      -- 'manual' | 'schedule'
);

CREATE INDEX IF NOT EXISTS idx_pipeline_history_type_started
    ON pipeline_history (pipeline_type, started_at DESC);
