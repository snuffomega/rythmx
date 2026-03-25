-- 003_enrichment_meta.sql
-- Tracks enrichment status per source per entity.
-- Enables resumable enrichment (skip not_found rows), per-source auditing,
-- and pluggable new enrichment sources without schema changes.

CREATE TABLE IF NOT EXISTS enrichment_meta (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,   -- 'itunes' | 'deezer' | 'musicbrainz' | 'spotify'
    entity_type  TEXT NOT NULL,   -- 'artist' | 'album'
    entity_id    TEXT NOT NULL,   -- lib_artists.id or lib_albums.id (Plex ratingKey)
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | found | not_found | error
    enriched_at  TEXT,
    error_msg    TEXT,
    UNIQUE(source, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_meta_entity
    ON enrichment_meta(entity_type, entity_id);
