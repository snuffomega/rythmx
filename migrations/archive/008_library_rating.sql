-- 008_library_rating.sql
--
-- Adds track rating and play count to lib_tracks.
-- rating: 0-10 Rythmx-native scale (single source of truth).
--   Plex:       0-10 (imported direct on library sync)
--   Navidrome:  1-5  (multiply × 2 on sync → stored as 0-10)
--   Jellyfin:   platform scale normalized on sync
--   SoulSync:   API source, no native ratings — defaults 0
-- Write-back to platform is Phase 14+ (stub in library sync service).

ALTER TABLE lib_tracks ADD COLUMN rating     INTEGER DEFAULT 0;
ALTER TABLE lib_tracks ADD COLUMN play_count INTEGER DEFAULT 0;
