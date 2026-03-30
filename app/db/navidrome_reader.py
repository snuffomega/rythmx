"""
navidrome_reader.py — Navidrome library platform reader.

Implements the library reader interface using the OpenSubsonic API.
All lib_* writes use additive merge (INSERT OR IGNORE + targeted UPDATE with
COALESCE guards) to preserve enrichment data across re-syncs.

Rating normalization: Navidrome 0-5 → stored as 0-10 to match Plex scale.
Source platform tag: source_platform = 'navidrome' on all rows.
MusicBrainzId written directly in Stage 1 from file tags — short-circuits Stage 2b.

SoulSync (discovery pool, similar artists) is Plex-only; these functions return
safe empty values, and enrichment falls through to Stage 2 normally.
"""
import json
import logging
import sqlite3
import time

from app import config

logger = logging.getLogger(__name__)


def _connect():
    """Return a WAL-mode connection to rythmx.db."""
    conn = sqlite3.connect(config.RYTHMX_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_client():
    """Build and return a NavidromeClient using config + settings-DB override."""
    from app.clients.navidrome_client import NavidromeClient
    from app.db import rythmx_store

    url = rythmx_store.get_setting("navidrome_url") or config.NAVIDROME_URL
    user = rythmx_store.get_setting("navidrome_user") or config.NAVIDROME_USER
    password = rythmx_store.get_setting("navidrome_pass") or config.NAVIDROME_PASS

    if not url or not user or not password:
        raise ValueError(
            "Navidrome not configured. Set NAVIDROME_URL, NAVIDROME_USER, "
            "NAVIDROME_PASS in .env (or via Settings UI)."
        )
    return NavidromeClient(url, user, password)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_library() -> dict:
    """Walk the Navidrome library via OpenSubsonic API and merge lib_* tables.

    Uses additive merge (INSERT OR IGNORE + targeted UPDATE) to preserve all
    enrichment data across re-syncs. Writes MusicBrainzId, BPM, replayGain,
    audio quality fields, and genres directly from file tags (Stage 1 bonus pass).

    Rating normalization: Navidrome userRating (0-5) is stored as 0-10 to
    maintain consistency with Plex's 0-10 scale. Multiply by 2 on ingest.

    Soft-delete: items absent from this sync get removed_at = CURRENT_TIMESTAMP.
    Micro-batched: commits every 50 artists.
    """
    client = _get_client()
    start = time.time()

    artist_count = 0
    album_count = 0
    track_count = 0

    with _connect() as conn:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _seen_artists (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _seen_albums  (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _seen_tracks  (id TEXT PRIMARY KEY)")

        all_artists = client.get_artists()

        for i, nav_artist in enumerate(all_artists):
            artist_id = nav_artist["id"]
            artist_name = nav_artist.get("name", "")
            cover_art = nav_artist.get("coverArt") or None
            mbid = nav_artist.get("musicBrainzId") or None
            genres_list = [g["name"] for g in nav_artist.get("genres", []) if g.get("name")]
            genres_json = json.dumps(genres_list) if genres_list else None

            conn.execute(
                "INSERT OR IGNORE INTO lib_artists "
                "(id, name, name_lower, source_platform, updated_at) "
                "VALUES (?, ?, ?, 'navidrome', CURRENT_TIMESTAMP)",
                (artist_id, artist_name, artist_name.lower()),
            )
            conn.execute(
                "UPDATE lib_artists SET name = ?, name_lower = ?, "
                "source_platform = 'navidrome', updated_at = CURRENT_TIMESTAMP, "
                "removed_at = NULL, "
                "thumb_url_navidrome = COALESCE(?, thumb_url_navidrome), "
                "musicbrainz_id = COALESCE(?, musicbrainz_id), "
                "genres_json_navidrome = COALESCE(?, genres_json_navidrome) "
                "WHERE id = ?",
                (artist_name, artist_name.lower(),
                 cover_art, mbid, genres_json,
                 artist_id),
            )
            conn.execute("INSERT OR IGNORE INTO _seen_artists (id) VALUES (?)", (artist_id,))
            artist_count += 1

            try:
                artist_detail = client.get_artist(artist_id)
            except Exception as exc:
                logger.warning("navidrome_reader: failed to fetch artist %s: %s", artist_id, exc)
                continue

            for nav_album in artist_detail.get("album", []):
                album_id = nav_album["id"]
                album_title = nav_album.get("name", "")
                album_year = nav_album.get("year") or None

                try:
                    album_detail = client.get_album(album_id)
                except Exception as exc:
                    logger.warning("navidrome_reader: failed to fetch album %s: %s", album_id, exc)
                    continue

                album_cover = album_detail.get("coverArt") or None
                album_mbid = album_detail.get("musicBrainzId") or None
                album_genres = [g["name"] for g in album_detail.get("genres", []) if g.get("name")]
                album_genres_json = json.dumps(album_genres) if album_genres else None

                conn.execute(
                    "INSERT OR IGNORE INTO lib_albums "
                    "(id, artist_id, title, local_title, title_lower, year, "
                    "source_platform, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'navidrome', CURRENT_TIMESTAMP)",
                    (album_id, artist_id, album_title, album_title,
                     album_title.lower(), album_year),
                )
                conn.execute(
                    "UPDATE lib_albums SET title = ?, local_title = ?, title_lower = ?, "
                    "year = COALESCE(?, year), source_platform = 'navidrome', "
                    "updated_at = CURRENT_TIMESTAMP, removed_at = NULL, "
                    "thumb_url_navidrome = COALESCE(?, thumb_url_navidrome), "
                    "musicbrainz_id = COALESCE(?, musicbrainz_id), "
                    "genres_json_navidrome = COALESCE(?, genres_json_navidrome) "
                    "WHERE id = ?",
                    (album_title, album_title, album_title.lower(), album_year,
                     album_cover, album_mbid, album_genres_json,
                     album_id),
                )
                conn.execute("INSERT OR IGNORE INTO _seen_albums (id) VALUES (?)", (album_id,))
                album_count += 1

                for song in album_detail.get("song", []):
                    track_id = song["id"]
                    track_title = song.get("title", "")
                    track_number = song.get("track") or None
                    disc_number = song.get("discNumber") or None
                    # duration in seconds from Subsonic — convert to ms to match Plex/lib_tracks
                    track_duration_s = song.get("duration") or None
                    duration_ms = int(track_duration_s * 1000) if track_duration_s else None
                    file_path = song.get("path") or None
                    file_size = song.get("size") or None
                    play_count = song.get("playCount") or None
                    track_mbid = song.get("musicBrainzId") or None

                    # Rating: Navidrome 0-5 → normalize to 0-10
                    raw_rating = song.get("userRating")
                    rating = float(raw_rating * 2) if raw_rating is not None else None

                    # OpenSubsonic audio quality fields
                    sample_rate = song.get("samplingRate") or None
                    bit_depth = song.get("bitDepth") or None
                    channel_count = song.get("channelCount") or None
                    bpm = song.get("bpm") or None

                    # replayGain object (OpenSubsonic extension)
                    rg = song.get("replayGain") or {}
                    rg_track = rg.get("trackGain")
                    rg_album = rg.get("albumGain")
                    rg_track_peak = rg.get("trackPeak")
                    rg_album_peak = rg.get("albumPeak")

                    conn.execute(
                        "INSERT OR IGNORE INTO lib_tracks "
                        "(id, album_id, artist_id, title, title_lower, track_number, "
                        "disc_number, duration, file_path, file_size, rating, play_count, "
                        "source_platform, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'navidrome', CURRENT_TIMESTAMP)",
                        (track_id, album_id, artist_id, track_title, track_title.lower(),
                         track_number, disc_number, duration_ms, file_path, file_size,
                         rating, play_count),
                    )
                    # NOTE: rating and play_count are bare assignments (not COALESCE-guarded).
                    # Navidrome is the authoritative source for these values on its own rows —
                    # same pattern as plex_reader.py. If Navidrome returns NULL (unrated), that
                    # is the correct value for this platform's data.
                    conn.execute(
                        "UPDATE lib_tracks SET title = ?, title_lower = ?, "
                        "track_number = ?, disc_number = ?, duration = ?, "
                        "file_path = ?, file_size = ?, "
                        "rating = ?, play_count = ?, "
                        "source_platform = 'navidrome', "
                        "updated_at = CURRENT_TIMESTAMP, removed_at = NULL, "
                        "sample_rate = COALESCE(?, sample_rate), "
                        "bit_depth = COALESCE(?, bit_depth), "
                        "channel_count = COALESCE(?, channel_count), "
                        "tempo_navidrome = COALESCE(?, tempo_navidrome), "
                        "replay_gain_track = COALESCE(?, replay_gain_track), "
                        "replay_gain_album = COALESCE(?, replay_gain_album), "
                        "replay_gain_track_peak = COALESCE(?, replay_gain_track_peak), "
                        "replay_gain_album_peak = COALESCE(?, replay_gain_album_peak), "
                        "musicbrainz_id = COALESCE(?, musicbrainz_id) "
                        "WHERE id = ?",
                        (track_title, track_title.lower(), track_number, disc_number,
                         duration_ms, file_path, file_size, rating, play_count,
                         sample_rate, bit_depth, channel_count, bpm,
                         rg_track, rg_album, rg_track_peak, rg_album_peak,
                         track_mbid,
                         track_id),
                    )
                    conn.execute("INSERT OR IGNORE INTO _seen_tracks (id) VALUES (?)", (track_id,))
                    track_count += 1

            if (i + 1) % 50 == 0:
                conn.commit()
                logger.debug("navidrome sync: committed batch at artist %d", i + 1)

        # Tombstone items not seen in this sync
        conn.execute(
            "UPDATE lib_tracks SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = 'navidrome' AND removed_at IS NULL "
            "AND id NOT IN (SELECT id FROM _seen_tracks)"
        )
        conn.execute(
            "UPDATE lib_albums SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = 'navidrome' AND removed_at IS NULL "
            "AND id NOT IN (SELECT id FROM _seen_albums)"
        )
        conn.execute(
            "UPDATE lib_artists SET removed_at = CURRENT_TIMESTAMP "
            "WHERE source_platform = 'navidrome' AND removed_at IS NULL "
            "AND id NOT IN (SELECT id FROM _seen_artists)"
        )

        duration_s = round(time.time() - start, 1)
        meta = {
            "last_synced_ts": str(int(time.time())),
            "track_count": str(track_count),
            "album_count": str(album_count),
            "artist_count": str(artist_count),
            "sync_duration_s": str(duration_s),
        }
        for key, value in meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO lib_meta (key, value) VALUES (?, ?)", (key, value)
            )

    logger.info(
        "navidrome_reader.sync_library: %d artists, %d albums, %d tracks in %.1fs",
        artist_count, album_count, track_count, duration_s,
    )
    return {
        "track_count": track_count,
        "album_count": album_count,
        "artist_count": artist_count,
        "sync_duration_s": duration_s,
    }


# ---------------------------------------------------------------------------
# Identity / ownership stubs (Task 4)
# ---------------------------------------------------------------------------

def is_db_accessible() -> bool:
    return False


def get_track_count() -> int:
    return 0


def get_native_artist_id(artist_name: str):
    return None


def get_spotify_artist_id(artist_name: str):
    return None


def get_deezer_artist_id(artist_name: str):
    return None


def get_itunes_artist_id(artist_name: str):
    return None


def check_album_owned(*args, **kwargs):
    return None


def check_owned_exact(spotify_track_id: str):
    return None


def check_owned_deezer(deezer_track_id: str):
    return None


def find_track_by_name(artist_name: str, track_title: str):
    return None


def get_all_tracks_for_artist(artist_id: str) -> list:
    return []


def get_tracks_for_album(artist_id: str, album_title: str) -> list:
    return []


def get_discovery_pool(**kwargs) -> list:
    return []


def get_similar_artists_map(**kwargs) -> dict:
    return {}
