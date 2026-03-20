-- 012: Persist API-fetched artist album catalogs for gap analysis.
-- During enrichment validation, iTunes/Deezer album catalogs are fetched to
-- score artist identity confidence. This table stores those catalogs so
-- downstream features (missing-album hints, dashboard gaps, discovery) can
-- diff against lib_albums without additional API calls.

CREATE TABLE IF NOT EXISTS lib_artist_catalog (
    artist_id   TEXT    NOT NULL,
    source      TEXT    NOT NULL,   -- 'itunes' or 'deezer'
    album_id    TEXT    NOT NULL,   -- source-specific album ID
    album_title TEXT    NOT NULL,
    record_type TEXT,               -- Deezer: album/single/compile | iTunes: NULL
    fetched_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, source, album_id)
);
