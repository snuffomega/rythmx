-- 017_clear_plex_record_type.sql
-- Plex reports album.type = "album" for everything (albums, EPs, singles).
-- Clear so query-time track-count heuristic can properly classify.
UPDATE lib_albums SET record_type = NULL
WHERE source_platform = 'plex' AND record_type = 'album';
