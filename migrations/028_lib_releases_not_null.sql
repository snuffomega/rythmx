-- 028: Enforce artist_id NOT NULL on lib_releases via table rebuild
-- Branch: db-audit
-- Prerequisite: Run scripts/backfill_artist_id.py first (zero NULL artist_id rows)
-- See: local-notes/db-audit/central-plan.md, DB-AUDIT-REVIEW.md

-- ═══════════════════════════════════════════════════════════════
-- STEP 1: Rebuild lib_releases with artist_id NOT NULL
-- ═══════════════════════════════════════════════════════════════

PRAGMA foreign_keys=OFF;

-- Safety guard: drop any leftover table from a previously interrupted run.
-- Python's sqlite3 auto-commits DDL, so a partial run can leave lib_releases_new
-- behind even after conn.rollback(). This makes the migration idempotent.
DROP TABLE IF EXISTS lib_releases_new;

CREATE TABLE lib_releases_new (
    id                           TEXT PRIMARY KEY,
    artist_id                    TEXT NOT NULL,     -- ← ENFORCED (was nullable)
    artist_name                  TEXT NOT NULL,
    artist_name_lower            TEXT NOT NULL,
    title                        TEXT NOT NULL,
    title_lower                  TEXT NOT NULL,
    normalized_title             TEXT,
    version_type                 TEXT DEFAULT 'original',
    kind_deezer                  TEXT,
    kind_itunes                  TEXT,
    thumb_url_deezer             TEXT,
    thumb_url_itunes             TEXT,
    release_date_deezer          TEXT,
    release_date_itunes          TEXT,
    itunes_album_id              TEXT,
    deezer_album_id              TEXT,
    spotify_album_id             TEXT,
    track_count                  INTEGER,
    catalog_source               TEXT,
    confidence                   INTEGER DEFAULT 0,
    is_owned                     INTEGER DEFAULT 0,
    owned_checked_at             TEXT,
    user_dismissed               INTEGER DEFAULT 0,
    explicit                     INTEGER DEFAULT 0,
    label                        TEXT,
    genre_itunes                 TEXT,
    genre_deezer                 TEXT,
    upc_deezer                   TEXT,
    canonical_release_id         TEXT,
    musicbrainz_release_group_id TEXT,
    original_release_date        TEXT,
    lastfm_tags_json             TEXT,
    first_seen_at                TEXT DEFAULT (datetime('now')),
    last_checked_at              TEXT
);

INSERT INTO lib_releases_new SELECT * FROM lib_releases;

DROP TABLE lib_releases;

ALTER TABLE lib_releases_new RENAME TO lib_releases;

-- ═══════════════════════════════════════════════════════════════
-- STEP 2: Recreate ALL indexes (existing from genesis + new from 027)
-- ═══════════════════════════════════════════════════════════════

-- Existing indexes (from 000_genesis.sql)
CREATE INDEX IF NOT EXISTS idx_lib_releases_artist
    ON lib_releases(artist_name_lower);
CREATE INDEX IF NOT EXISTS idx_lib_releases_date
    ON lib_releases(release_date_deezer);
CREATE INDEX IF NOT EXISTS idx_lib_releases_artist_id
    ON lib_releases(artist_id);
CREATE INDEX IF NOT EXISTS idx_lib_releases_canonical
    ON lib_releases(canonical_release_id) WHERE canonical_release_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_releases_norm_title
    ON lib_releases(artist_name_lower, normalized_title);
CREATE INDEX IF NOT EXISTS idx_lib_releases_missing
    ON lib_releases(is_owned, artist_id) WHERE is_owned = 0;

-- New indexes (from 027_schema_hardening.sql — must recreate after table rebuild)
CREATE INDEX IF NOT EXISTS idx_lib_releases_deezer_album_id
    ON lib_releases(deezer_album_id) WHERE deezer_album_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_releases_itunes_album_id
    ON lib_releases(itunes_album_id) WHERE itunes_album_id IS NOT NULL;

-- NOTE: enforcement triggers are applied by init_db() via executescript().
-- The migration runner splits SQL on semicolons and cannot handle
-- multi-statement DDL (trigger bodies contain internal semicolons).

ANALYZE;
