-- Migration 007: Fetch control-plane tables (provider-agnostic, Tidarr first)
-- Adds run/task persistence for Forge fetch orchestration.

CREATE TABLE IF NOT EXISTS fetch_runs (
    id           TEXT PRIMARY KEY,
    build_id     TEXT    NOT NULL,
    provider     TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'running',
    triggered_by TEXT    NOT NULL DEFAULT 'manual',
    total_tasks  INTEGER NOT NULL DEFAULT 0,
    config_json  TEXT    NOT NULL DEFAULT '{}',
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    last_error   TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fetch_runs_build
    ON fetch_runs (build_id);

CREATE INDEX IF NOT EXISTS idx_fetch_runs_status
    ON fetch_runs (status);

CREATE INDEX IF NOT EXISTS idx_fetch_runs_created
    ON fetch_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS fetch_tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT    NOT NULL,
    build_id           TEXT    NOT NULL,
    provider           TEXT    NOT NULL,
    artist_name        TEXT    NOT NULL,
    album_name         TEXT    NOT NULL,
    artist_key         TEXT    NOT NULL,
    album_key          TEXT    NOT NULL,
    stage              TEXT    NOT NULL DEFAULT 'queued',
    provider_job_id    TEXT,
    metadata_json      TEXT    NOT NULL DEFAULT '{}',
    storage_path       TEXT,
    source_dir         TEXT,
    dest_dir           TEXT,
    error_type         TEXT,
    error_code         TEXT,
    error_message      TEXT,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    scan_deadline_at   TEXT,
    completed_at       TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    last_transition_at TEXT    NOT NULL,
    FOREIGN KEY (run_id) REFERENCES fetch_runs(id) ON DELETE CASCADE,
    UNIQUE (run_id, artist_key, album_key)
);

CREATE INDEX IF NOT EXISTS idx_fetch_tasks_run
    ON fetch_tasks (run_id);

CREATE INDEX IF NOT EXISTS idx_fetch_tasks_stage
    ON fetch_tasks (stage);

CREATE INDEX IF NOT EXISTS idx_fetch_tasks_provider_job
    ON fetch_tasks (provider, provider_job_id);

CREATE INDEX IF NOT EXISTS idx_fetch_tasks_build
    ON fetch_tasks (build_id);
