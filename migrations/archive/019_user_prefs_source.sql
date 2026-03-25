-- 019_user_prefs_source.sql
-- Track whether a dismiss was auto (compilation pattern) or manual (user action).

ALTER TABLE user_release_prefs ADD COLUMN source TEXT DEFAULT 'manual';
