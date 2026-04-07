-- Migration 006: Download jobs — tracks plugin-based fetch jobs initiated from Forge builds.
-- Separate from download_queue (CC pipeline acquisition queue).
-- Each row represents one plugin submit() call for one unique album in a build.

CREATE TABLE IF NOT EXISTS download_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id     TEXT    NOT NULL,
    job_id       TEXT    NOT NULL,
    provider     TEXT    NOT NULL,
    artist_name  TEXT    NOT NULL,
    album_name   TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    storage_path TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_download_jobs_build_job
    ON download_jobs (build_id, job_id);

CREATE INDEX IF NOT EXISTS idx_download_jobs_build
    ON download_jobs (build_id);

CREATE INDEX IF NOT EXISTS idx_download_jobs_status
    ON download_jobs (status);
