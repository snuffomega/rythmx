-- 027: Schema hardening — new indexes + audit infrastructure
-- Branch: db-audit
-- See: local-notes/db-audit/central-plan.md, DB-AUDIT-REVIEW.md

-- ═══════════════════════════════════════════════════════════════
-- PART 1: New indexes (partial — skip NULLs for efficiency)
-- Indexes that already exist in 000_genesis.sql are NOT repeated:
--   idx_lib_artists_lastfm_mbid, idx_lib_artists_deezer_artist_id,
--   idx_lib_artists_name_lower, idx_lib_releases_artist_id
-- ═══════════════════════════════════════════════════════════════

-- Stage 2b: MusicBrainz rich worker lookups
CREATE INDEX IF NOT EXISTS idx_lib_artists_musicbrainz_id
    ON lib_artists(musicbrainz_id) WHERE musicbrainz_id IS NOT NULL;

-- idx_lib_albums_deezer already exists in 000_genesis.sql — skip

-- Stage 3: Deezer BPM worker (manual only) uses track-level deezer_id
CREATE INDEX IF NOT EXISTS idx_lib_tracks_deezer_id
    ON lib_tracks(deezer_id) WHERE deezer_id IS NOT NULL;

-- Stage 3: rich_deezer.py UPC/genre update + ownership_sync.py Pass 1
CREATE INDEX IF NOT EXISTS idx_lib_releases_deezer_album_id
    ON lib_releases(deezer_album_id) WHERE deezer_album_id IS NOT NULL;

-- Ownership sync Pass 1: EXISTS subquery on itunes_album_id
CREATE INDEX IF NOT EXISTS idx_lib_releases_itunes_album_id
    ON lib_releases(itunes_album_id) WHERE itunes_album_id IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════
-- PART 2: Audit infrastructure
-- ═══════════════════════════════════════════════════════════════

-- Logs corrective actions during backfill and future migrations
CREATE TABLE IF NOT EXISTS migration_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id     TEXT,
    details    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Captures attempted NULL FK writes before NOT NULL is enforced
CREATE TABLE IF NOT EXISTS fk_violation_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op         TEXT NOT NULL,
    row_id     TEXT,
    payload    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

ANALYZE;
