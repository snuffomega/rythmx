-- Per-source artwork columns: store everything, resolve at delivery.
-- Same pattern as kind_deezer/kind_itunes on lib_releases (migration 016).

-- lib_artists: per-source artist photos
ALTER TABLE lib_artists ADD COLUMN image_url_fanart TEXT;
ALTER TABLE lib_artists ADD COLUMN image_url_deezer TEXT;

-- lib_albums: per-source album artwork
ALTER TABLE lib_albums ADD COLUMN thumb_url_plex TEXT;
ALTER TABLE lib_albums ADD COLUMN thumb_url_deezer TEXT;

-- Migrate existing data.
-- Artist: can't distinguish source after the fact; copy to deezer (guaranteed fallback).
-- Next enrichment run populates fanart column correctly for MBID-matched artists.
UPDATE lib_artists SET image_url_deezer = image_url WHERE image_url IS NOT NULL;

-- Albums: HTTP URLs are Deezer CDN; anything else is a Plex relative path.
UPDATE lib_albums SET thumb_url_deezer = thumb_url WHERE thumb_url LIKE 'http%';
UPDATE lib_albums SET thumb_url_plex = thumb_url WHERE thumb_url IS NOT NULL AND thumb_url NOT LIKE 'http%';

-- Clear stale enrichment_meta so pipeline re-enriches with per-source writes.
DELETE FROM enrichment_meta WHERE source = 'artist_art';
DELETE FROM enrichment_meta WHERE source = 'deezer_rich';
