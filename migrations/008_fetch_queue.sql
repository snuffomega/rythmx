-- Migration 008: Fetch queue + run handoff/cancel metadata.
-- Adds FIFO queue control plane for Build & Fetch and augments fetch_runs
-- with queue linkage, handoff lifecycle, and terminal-event dedupe fields.

CREATE TABLE IF NOT EXISTS fetch_queue (
    id              TEXT PRIMARY KEY,
    build_id        TEXT    NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'build_fetch',
    payload_json    TEXT    NOT NULL DEFAULT '{}',
    status          TEXT    NOT NULL DEFAULT 'pending',
    queue_position  INTEGER NOT NULL DEFAULT 0,
    run_id          TEXT,
    requested_by    TEXT    NOT NULL DEFAULT 'manual',
    started_at      TEXT,
    finished_at     TEXT,
    last_error      TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fetch_queue_status_created
    ON fetch_queue (status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_fetch_queue_build
    ON fetch_queue (build_id);

CREATE INDEX IF NOT EXISTS idx_fetch_queue_run
    ON fetch_queue (run_id);

ALTER TABLE fetch_runs ADD COLUMN queue_id TEXT;
ALTER TABLE fetch_runs ADD COLUMN handoff_status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE fetch_runs ADD COLUMN handoff_started_at TEXT;
ALTER TABLE fetch_runs ADD COLUMN handoff_finished_at TEXT;
ALTER TABLE fetch_runs ADD COLUMN handoff_error TEXT;
ALTER TABLE fetch_runs ADD COLUMN terminal_emitted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fetch_runs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_fetch_runs_queue
    ON fetch_runs (queue_id);

CREATE INDEX IF NOT EXISTS idx_fetch_runs_handoff
    ON fetch_runs (handoff_status);
