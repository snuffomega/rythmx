-- 033: Navidrome audio quality columns
-- Adds per-file audio metadata (Navidrome/OpenSubsonic source) and
-- Navidrome-specific in-kind columns. All nullable — Plex rows keep NULL here.
--
-- The migration runner skips "duplicate column name" errors, so each
-- ALTER TABLE is safe to retry on partial failure or out-of-band additions.

-- lib_tracks: audio quality
ALTER TABLE lib_tracks ADD COLUMN sample_rate INTEGER;
ALTER TABLE lib_tracks ADD COLUMN bit_depth INTEGER;
ALTER TABLE lib_tracks ADD COLUMN channel_count INTEGER;

-- lib_tracks: ReplayGain
ALTER TABLE lib_tracks ADD COLUMN replay_gain_track REAL;
ALTER TABLE lib_tracks ADD COLUMN replay_gain_album REAL;
ALTER TABLE lib_tracks ADD COLUMN replay_gain_track_peak REAL;
ALTER TABLE lib_tracks ADD COLUMN replay_gain_album_peak REAL;

-- lib_tracks: Navidrome BPM (in-kind, separate from tempo_deezer)
ALTER TABLE lib_tracks ADD COLUMN tempo_navidrome REAL;

-- lib_artists: Navidrome cover art + multi-genre array from file tags
ALTER TABLE lib_artists ADD COLUMN thumb_url_navidrome TEXT;
ALTER TABLE lib_artists ADD COLUMN genres_json_navidrome TEXT;

-- lib_albums: Navidrome cover art + multi-genre array
ALTER TABLE lib_albums ADD COLUMN thumb_url_navidrome TEXT;
ALTER TABLE lib_albums ADD COLUMN genres_json_navidrome TEXT;

-- lib_albums: Navidrome MBID (lib_artists already has musicbrainz_id from 000_genesis)
ALTER TABLE lib_albums ADD COLUMN musicbrainz_id TEXT;

-- Partial index on musicbrainz_id — mirrors the pattern from 027_schema_hardening.sql
CREATE INDEX IF NOT EXISTS idx_lib_albums_musicbrainz_id
    ON lib_albums(musicbrainz_id) WHERE musicbrainz_id IS NOT NULL;
