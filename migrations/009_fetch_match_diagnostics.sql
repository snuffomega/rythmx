-- Migration 009: Fetch match diagnostics + manual release selection metadata.
-- Adds persisted task-level match fields used by fetch activity and probe tooling.

ALTER TABLE fetch_tasks ADD COLUMN match_status TEXT;
ALTER TABLE fetch_tasks ADD COLUMN match_strategy TEXT;
ALTER TABLE fetch_tasks ADD COLUMN match_confidence REAL;
ALTER TABLE fetch_tasks ADD COLUMN match_reasons_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE fetch_tasks ADD COLUMN match_candidates_json TEXT NOT NULL DEFAULT '[]';

