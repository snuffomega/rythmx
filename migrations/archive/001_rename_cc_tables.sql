-- Migration 001: rename legacy cc_ tables to clean names
-- Phase 9 terminology cleanup: cc_ prefix was from when Cruise Control was the whole app.
-- Rythmx.db owns all tables now; table names should reflect the data, not the feature.
--
-- This migration is idempotent: if old tables don't exist (fresh install),
-- each ALTER TABLE is skipped gracefully by the migration runner.

ALTER TABLE cc_history RENAME TO history;
ALTER TABLE cc_playlist RENAME TO playlist_tracks;
ALTER TABLE cc_taste_cache RENAME TO taste_cache;
ALTER TABLE cc_settings RENAME TO app_settings;
ALTER TABLE cc_candidates RENAME TO candidates;

-- Migrate settings keys: strip cc_ prefix from all cc_* keys
-- Non-cc_ keys (nr_ignore_*, release_cache_refresh_*) are unaffected by the WHERE clause
INSERT OR IGNORE INTO app_settings (key, value)
    SELECT replace(key, 'cc_', ''), value
    FROM app_settings
    WHERE key LIKE 'cc_%';

-- Migrate playlist source literal (source column lives on playlists metadata table)
UPDATE playlists SET source = 'new_music' WHERE source = 'cc'
