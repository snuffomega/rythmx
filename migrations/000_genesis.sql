-- =========================================================================
-- 000_genesis.sql — Rythmx Complete Schema (Data-First Refactor)
--
-- Replaces incremental migrations 001–026.
-- Represents the final-state schema with all V2 In-Kind Storage rules
-- applied from the start. Delete rythmx.db and restart to apply.
--
-- Created: 2026-03-24
-- =========================================================================

-- =========================================================================
-- LIBRARY TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS lib_artists (
    id                   TEXT PRIMARY KEY,       -- Platform ratingKey (Plex/Navidrome)
    name                 TEXT NOT NULL,
    name_lower           TEXT NOT NULL,
    -- Per-source IDs (In-Kind Rule #2: unique identifiers, one column per source)
    itunes_artist_id     TEXT,
    deezer_artist_id     TEXT,
    spotify_artist_id    TEXT,
    musicbrainz_id       TEXT,
    lastfm_mbid          TEXT,
    -- Enrichment quality
    match_confidence     INTEGER DEFAULT 0,      -- 0–100
    needs_verification   INTEGER DEFAULT 0,      -- 1 = audit queue
    conflict_flags       TEXT,                    -- JSON
    -- Per-source rich data (In-Kind Rule #1)
    genres_json_spotify  TEXT,                    -- JSON array from Spotify
    popularity_spotify   INTEGER,                -- 0–100 from Spotify
    lastfm_tags_json     TEXT,                    -- JSON array, normalized canonical tags
    listener_count_lastfm INTEGER,               -- Last.fm global listeners
    play_count_lastfm    INTEGER,                -- Last.fm global scrobbles
    bio_lastfm           TEXT,                    -- Last.fm artist bio (HTML stripped)
    fans_deezer          INTEGER,                -- Deezer nb_fan count
    similar_artists_json TEXT,                    -- JSON array [{name, match, source}, ...]
    area_musicbrainz     TEXT,                    -- MusicBrainz area (country/region)
    begin_area_musicbrainz TEXT,                  -- MusicBrainz begin area (city/origin)
    formed_year_musicbrainz INTEGER,             -- MusicBrainz life-span begin year
    -- Per-source artwork (In-Kind Rule #1)
    image_url_fanart     TEXT,                    -- Fanart.tv artist photo
    image_url_deezer     TEXT,                    -- Deezer picture_xl fallback
    -- Computed
    missing_count        INTEGER DEFAULT 0,      -- Precomputed missing release count
    -- Platform metadata
    source_platform      TEXT DEFAULT 'plex',    -- plex | navidrome | jellyfin | file
    removed_at           TEXT,                    -- Tombstone (soft-delete on re-sync)
    added_at             TEXT,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lib_artists_name_lower
    ON lib_artists(name_lower);
CREATE INDEX IF NOT EXISTS idx_lib_artists_deezer_artist_id
    ON lib_artists(deezer_artist_id) WHERE deezer_artist_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_artists_lastfm_mbid
    ON lib_artists(lastfm_mbid) WHERE lastfm_mbid IS NOT NULL;
-- Added mig 027: MusicBrainz ID lookups for Stage 2b + Stage 3
CREATE INDEX IF NOT EXISTS idx_lib_artists_musicbrainz_id
    ON lib_artists(musicbrainz_id) WHERE musicbrainz_id IS NOT NULL;

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lib_albums (
    id                      TEXT PRIMARY KEY,    -- Platform ratingKey
    artist_id               TEXT NOT NULL,       -- FK → lib_artists.id
    title                   TEXT NOT NULL,
    local_title             TEXT,                -- Plex title, preserved for conflict detection
    api_title               TEXT,                -- iTunes/Deezer corrected title
    title_lower             TEXT NOT NULL,
    year                    INTEGER,
    -- Per-source IDs
    itunes_album_id         TEXT,
    deezer_id               TEXT,
    spotify_album_id        TEXT,
    musicbrainz_release_id  TEXT,
    -- Enrichment quality
    match_confidence        INTEGER DEFAULT 0,
    needs_verification      INTEGER DEFAULT 0,
    conflict_flags          TEXT,
    -- Per-source rich data (In-Kind Rule #1)
    record_type_deezer      TEXT,                -- album | single | compile (Deezer)
    genre_itunes            TEXT,                -- primaryGenreName (iTunes)
    release_date_itunes     TEXT,                -- YYYY-MM-DD (iTunes)
    lastfm_tags_json        TEXT,                -- JSON array, canonical tags
    -- Per-source artwork (In-Kind Rule #1)
    thumb_url_plex          TEXT,                -- Plex relative .thumb path
    thumb_url_deezer        TEXT,                -- Deezer CDN URL
    -- Plex-specific metadata
    label_plex              TEXT,
    plex_release_date       TEXT,
    last_viewed_at          TEXT,
    appears_on              INTEGER DEFAULT 0,   -- 1 = collab/feature
    -- Platform metadata
    source_platform         TEXT DEFAULT 'plex',
    removed_at              TEXT,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lib_albums_artist_title
    ON lib_albums(artist_id, title_lower);
CREATE INDEX IF NOT EXISTS idx_lib_albums_itunes
    ON lib_albums(itunes_album_id);
CREATE INDEX IF NOT EXISTS idx_lib_albums_deezer
    ON lib_albums(deezer_id);

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lib_tracks (
    id                TEXT PRIMARY KEY,           -- Platform ratingKey
    album_id          TEXT NOT NULL,              -- FK → lib_albums.id
    artist_id         TEXT NOT NULL,              -- FK → lib_artists.id
    title             TEXT NOT NULL,
    title_lower       TEXT NOT NULL,
    track_number      INTEGER,
    disc_number       INTEGER,
    duration          INTEGER,                    -- milliseconds
    file_path         TEXT,
    file_size         INTEGER,
    -- Per-source IDs
    spotify_track_id  TEXT,
    deezer_id         TEXT,
    itunes_track_id   TEXT,
    -- Per-source rich data (In-Kind Rule #1)
    tempo_deezer      REAL,                       -- BPM from Deezer (manual only)
    -- User/platform data
    match_confidence  INTEGER DEFAULT 0,
    rating            INTEGER DEFAULT 0,          -- 0–10 normalized
    play_count        INTEGER DEFAULT 0,
    skip_count        INTEGER DEFAULT 0,
    -- Platform metadata
    source_platform   TEXT DEFAULT 'plex',
    removed_at        TEXT,
    last_viewed_at    TEXT,
    added_at          TEXT,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_path, file_size)
);
-- Added mig 027: Deezer BPM worker lookups (manual-only Stage 3)
CREATE INDEX IF NOT EXISTS idx_lib_tracks_deezer_id
    ON lib_tracks(deezer_id) WHERE deezer_id IS NOT NULL;

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lib_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- =========================================================================
-- ENRICHMENT & CATALOG TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS enrichment_meta (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,                   -- itunes | deezer | spotify_id | lastfm_id | ...
    entity_type  TEXT NOT NULL,                   -- artist | album
    entity_id    TEXT NOT NULL,                   -- lib_artists.id or lib_albums.id
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | found | not_found | error | fallback
    enriched_at  TEXT,
    error_msg    TEXT,
    confidence   INTEGER,                         -- 0–100 per-result
    retry_after  TEXT,                             -- 30-day quiet retry for not_found
    verified_at  TEXT,                             -- Reconciliation: set after confirmed column write
    UNIQUE(source, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_meta_entity
    ON enrichment_meta(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_meta_retry
    ON enrichment_meta(retry_after) WHERE retry_after IS NOT NULL;

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lib_artist_catalog (
    artist_id   TEXT NOT NULL,
    source      TEXT NOT NULL,                    -- itunes | deezer
    album_id    TEXT NOT NULL,                    -- Source-specific album ID
    album_title TEXT NOT NULL,
    record_type TEXT,                              -- Deezer: album/single/compile | iTunes: NULL
    track_count INTEGER,
    artwork_url TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, source, album_id)
);

-- =========================================================================
-- RELEASE STORE (store everything, filter at delivery)
-- =========================================================================

CREATE TABLE IF NOT EXISTS lib_releases (
    id                           TEXT PRIMARY KEY, -- {source}_{album_id} e.g. deezer_12345
    artist_id                    TEXT NOT NULL,      -- FK → lib_artists.id (enforced mig 028)
    artist_name                  TEXT NOT NULL,
    artist_name_lower            TEXT NOT NULL,
    title                        TEXT NOT NULL,
    title_lower                  TEXT NOT NULL,
    normalized_title             TEXT,              -- strip_title_suffixes(title) for grouping
    version_type                 TEXT DEFAULT 'original', -- original | remaster | deluxe | ...
    -- Per-source kind (In-Kind Rule #1 — no legacy 'kind' column)
    kind_deezer                  TEXT,              -- Deezer record_type (authoritative)
    kind_itunes                  TEXT,              -- Derived from iTunes collectionType
    -- Per-source artwork (In-Kind Rule #1)
    thumb_url_deezer             TEXT,              -- Deezer CDN URL
    thumb_url_itunes             TEXT,              -- iTunes artwork URL
    -- Per-source release dates (In-Kind Rule #1)
    release_date_deezer          TEXT,              -- YYYY-MM-DD from Deezer
    release_date_itunes          TEXT,              -- YYYY-MM-DD from iTunes
    -- Per-source IDs
    itunes_album_id              TEXT,
    deezer_album_id              TEXT,
    spotify_album_id             TEXT,
    -- Catalog metadata
    track_count                  INTEGER,
    catalog_source               TEXT,              -- itunes | deezer — which source this row came from
    confidence                   INTEGER DEFAULT 0,
    -- Ownership
    is_owned                     INTEGER DEFAULT 0, -- 0=missing, 1=owned
    owned_checked_at             TEXT,
    user_dismissed               INTEGER DEFAULT 0, -- 1 = hidden from missing shelf
    -- Rich data
    explicit                     INTEGER DEFAULT 0,
    label                        TEXT,
    genre_itunes                 TEXT,               -- primaryGenreName
    genre_deezer                 TEXT,               -- Deezer genre
    upc_deezer                   TEXT,               -- UPC from Deezer /album/{id}
    -- Grouping
    canonical_release_id         TEXT,               -- Self-FK for version grouping
    -- Future enrichment (nullable)
    musicbrainz_release_group_id TEXT,
    original_release_date        TEXT,
    lastfm_tags_json             TEXT,
    -- Timestamps
    first_seen_at                TEXT DEFAULT (datetime('now')),
    last_checked_at              TEXT
);

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
-- Added mig 027: bridging-ID indexes for Stage 3 enrichment + ownership sync
CREATE INDEX IF NOT EXISTS idx_lib_releases_deezer_album_id
    ON lib_releases(deezer_album_id) WHERE deezer_album_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lib_releases_itunes_album_id
    ON lib_releases(itunes_album_id) WHERE itunes_album_id IS NOT NULL;
-- NOTE: enforcement triggers (trg_lib_releases_artistid_*) are created by
-- init_db() via executescript() — the migration runner cannot handle
-- multi-statement DDL (triggers) because it splits on semicolons.

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_release_prefs (
    release_id  TEXT NOT NULL PRIMARY KEY,
    dismissed   INTEGER DEFAULT 0,
    priority    INTEGER DEFAULT 0,
    source      TEXT DEFAULT 'manual',            -- manual | auto (compilation pattern)
    notes       TEXT,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- PIPELINE OBSERVABILITY
-- =========================================================================

CREATE TABLE IF NOT EXISTS pipeline_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_type TEXT NOT NULL,                   -- new_music | custom_discovery
    run_mode      TEXT NOT NULL,                   -- preview | build | fetch
    status        TEXT NOT NULL DEFAULT 'running', -- running | completed | error
    config_json   TEXT,                            -- Settings snapshot (JSON)
    started_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   TIMESTAMP,
    run_duration  REAL,                            -- seconds
    summary_json  TEXT,                            -- Result summary (JSON)
    error_message TEXT,
    triggered_by  TEXT NOT NULL DEFAULT 'manual'   -- manual | schedule
);

CREATE INDEX IF NOT EXISTS idx_pipeline_history_type_started
    ON pipeline_history(pipeline_type, started_at DESC);

-- =========================================================================
-- CACHING & IDENTITY
-- =========================================================================

CREATE TABLE IF NOT EXISTS spotify_raw_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query_type    TEXT NOT NULL,                   -- artist | audio_features | appears_on
    entity_id     TEXT NOT NULL,
    entity_name   TEXT,
    raw_json      TEXT NOT NULL,
    fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(query_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_spotify_raw_entity
    ON spotify_raw_cache(query_type, entity_id);

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS artist_identity_cache (
    lastfm_name        TEXT PRIMARY KEY,
    deezer_artist_id   TEXT,
    spotify_artist_id  TEXT,
    itunes_artist_id   TEXT,
    mb_artist_id       TEXT,
    soulsync_artist_id TEXT,
    resolution_method  TEXT,
    confidence         INTEGER DEFAULT 80,
    last_resolved_ts   INTEGER DEFAULT 0
);

-- =========================================================================
-- CORE ACQUISITION & PLAYLIST TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    track_name          TEXT,
    artist_name         TEXT,
    album_name          TEXT,
    source              TEXT,
    score               REAL,
    acquisition_status  TEXT,
    reason              TEXT,
    cycle_date          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_history_unique
    ON history(artist_name, album_name, cycle_date);

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS playlist_tracks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_name     TEXT DEFAULT 'For You',
    track_id          TEXT,
    spotify_track_id  TEXT,
    track_name        TEXT,
    artist_name       TEXT,
    album_name        TEXT,
    album_cover_url   TEXT,
    score             REAL,
    position          INT,
    added_date        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    plex_playlist_id  TEXT,
    is_owned          INTEGER DEFAULT 1,
    release_date      TEXT,
    UNIQUE(track_id)
);

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_track_id  TEXT UNIQUE,
    track_name        TEXT,
    artist_name       TEXT,
    album_name        TEXT,
    album_cover_url   TEXT,
    score             REAL,
    is_owned          INTEGER DEFAULT 0,
    plex_rating_key   TEXT,
    source            TEXT,
    scored_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS playlists (
    name          TEXT PRIMARY KEY,
    source        TEXT DEFAULT 'manual',
    source_url    TEXT,
    auto_sync     INTEGER DEFAULT 0,
    mode          TEXT DEFAULT 'library_only',
    last_synced_ts INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    max_tracks    INTEGER DEFAULT 50
);

-- =========================================================================
-- DOWNLOAD & QUEUE
-- =========================================================================

CREATE TABLE IF NOT EXISTS download_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_name       TEXT NOT NULL,
    album_title       TEXT NOT NULL,
    release_date      TEXT,
    kind              TEXT,
    source            TEXT,
    itunes_album_id   TEXT,
    deezer_album_id   TEXT,
    spotify_album_id  TEXT,
    status            TEXT DEFAULT 'pending',
    requested_by      TEXT,
    playlist_name     TEXT,
    provider_response TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(artist_name, album_title)
);

-- =========================================================================
-- SETTINGS & KEYS
-- =========================================================================

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- IMAGE & TASTE CACHES
-- =========================================================================

CREATE TABLE IF NOT EXISTS image_cache (
    entity_type   TEXT NOT NULL,
    entity_key    TEXT NOT NULL,
    image_url     TEXT DEFAULT '',
    last_accessed TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (entity_type, entity_key)
);

CREATE TABLE IF NOT EXISTS taste_cache (
    artist_name  TEXT PRIMARY KEY,
    play_count   INT,
    period       TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- AUDIT INFRASTRUCTURE (added mig 027)
-- =========================================================================

CREATE TABLE IF NOT EXISTS migration_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id     TEXT,
    details    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fk_violation_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op         TEXT NOT NULL,
    row_id     TEXT,
    payload    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- =========================================================================
-- FORGE TABLES (added mig 029–032)
-- =========================================================================

-- Tier 3 — Permanent: user-created playlists (never auto-purged)
CREATE TABLE IF NOT EXISTS forge_playlists (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    plex_push_at  TEXT
);

CREATE TABLE IF NOT EXISTS forge_playlist_tracks (
    playlist_id   TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    position      INTEGER NOT NULL,
    added_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_forge_playlist_tracks_playlist
    ON forge_playlist_tracks(playlist_id);

-- Tier 2 — Rebuildable: 2-hop similarity cache for Discovery tab
CREATE TABLE IF NOT EXISTS forge_similarity_graph (
    artist_id     TEXT NOT NULL,
    similar_name  TEXT NOT NULL,
    similar_name_lower TEXT NOT NULL,
    hop           INTEGER NOT NULL DEFAULT 1,
    score         REAL,
    source        TEXT,
    updated_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, similar_name_lower)
);

CREATE INDEX IF NOT EXISTS idx_forge_similarity_artist
    ON forge_similarity_graph(artist_id);

-- Tier 2 — Rebuildable: external artist cache (TTL 30d)
CREATE TABLE IF NOT EXISTS forge_discovered_artists (
    deezer_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    name_lower    TEXT NOT NULL,
    image_url     TEXT,
    fans_deezer   INTEGER,
    source_artist_id TEXT,
    fetched_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forge_discovered_artists_name
    ON forge_discovered_artists(name_lower);

-- Tier 2 — Rebuildable: recent releases from discovered artists (TTL 7d)
CREATE TABLE IF NOT EXISTS forge_discovered_releases (
    id              TEXT PRIMARY KEY,
    artist_deezer_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    record_type     TEXT,
    release_date    TEXT,
    cover_url       TEXT,
    fetched_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forge_discovered_releases_artist
    ON forge_discovered_releases(artist_deezer_id);
