-- Migration 011: Stage 3 rich data columns + enrichment_meta retry_after
-- Phase 15 — Library-First Pipeline Architecture
--
-- lib_albums: release_date (exact date from iTunes), genre (primary genre from iTunes)
-- lib_artists: listener_count + global_play_count (from Last.fm artist.getInfo)
-- enrichment_meta: retry_after (30-day quiet retry for not_found rows)

ALTER TABLE lib_albums ADD COLUMN release_date TEXT;
ALTER TABLE lib_albums ADD COLUMN genre TEXT;

ALTER TABLE lib_artists ADD COLUMN listener_count INTEGER;
ALTER TABLE lib_artists ADD COLUMN global_play_count INTEGER;

ALTER TABLE enrichment_meta ADD COLUMN retry_after TEXT;

-- Index for retry_after lookups (partial index — only rows that have a retry date)
CREATE INDEX IF NOT EXISTS idx_enrichment_meta_retry
    ON enrichment_meta(retry_after) WHERE retry_after IS NOT NULL;
