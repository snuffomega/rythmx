-- 040: Local artwork store metadata on image_cache (Phase 29e + 29h)
-- Adds:
--   - local_path: absolute path to stored original artwork bytes
--   - content_hash: SHA-256 hex hash for content-addressed local serving
--   - artwork_source: embedded | fanart | deezer | itunes | plex
--
-- Runner skips duplicate-column/index errors, so this is safe to re-run.

ALTER TABLE image_cache ADD COLUMN local_path TEXT;
ALTER TABLE image_cache ADD COLUMN content_hash TEXT;
ALTER TABLE image_cache ADD COLUMN artwork_source TEXT;

CREATE INDEX IF NOT EXISTS idx_image_cache_content_hash
    ON image_cache(content_hash) WHERE content_hash IS NOT NULL;
