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
import re
import sqlite3
import time
import unicodedata

from app import config

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize artist/album name for consistent matching across platforms.

    Handles Unicode punctuation differences between Navidrome (which preserves
    curly apostrophes, smart quotes, trailing punctuation) and Plex (which
    strips/normalizes them). Applied only to the *_lower matching fields —
    display names (name, title) are left unchanged.

    Steps:
    1. NFKD decomposition — splits combined Unicode characters into base + diacritic.
    2. ASCII encode/decode — drops non-ASCII bytes (curly quotes, smart quotes,
       accented chars that survive as bare letters after NFKD become ASCII;
       pure punctuation code-points are dropped).
    3. Strip trailing punctuation Plex ignores (!, ., ,, ;).
    4. Collapse internal whitespace left by dropped characters.
    5. Lowercase.
    """
    if not name:
        return name
    # Step 1+2: NFKD → ASCII-only (drops curly apostrophes, smart quotes, etc.)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    # Step 3: normalize & ↔ and (Plex may spell out or abbreviate)
    name = re.sub(r"\s*&\s*", " and ", name)
    # Step 4: strip trailing punctuation Plex silently drops
    name = re.sub(r"[!.,;]+$", "", name.strip())
    # Step 5: collapse runs of whitespace that may result from dropped chars
    name = re.sub(r"\s+", " ", name).strip()
    # Step 6: lowercase
    return name.lower()


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
        # Artist payloads from /getArtists are the cheapest source of owner metadata.
        # Keep them indexed so album/track owner IDs can be upserted without extra API
        # requests in the common path.
        artist_by_id: dict[str, dict] = {
            str(a["id"]): a for a in all_artists if a.get("id")
        }

        def _to_id(value) -> str:
            return str(value).strip() if value is not None and str(value).strip() else ""

        def _upsert_artist_row(
            owner_id: str,
            name_hint: str = "",
            cover_hint: str | None = None,
            mbid_hint: str | None = None,
            genres_hint: list[str] | None = None,
        ) -> None:
            """
            Ensure a lib_artists row exists for any album/track owner ID.

            Navidrome frequently exposes collaboration entities where album/track owner
            differs from the outer artist loop. We upsert by owner ID so ownership stays
            stable regardless of traversal order.
            """
            if not owner_id:
                return

            payload = artist_by_id.get(owner_id) or {}
            owner_name = (
                payload.get("name")
                or name_hint
                or owner_id
            )
            owner_cover = payload.get("coverArt") or cover_hint or None
            owner_mbid = payload.get("musicBrainzId") or mbid_hint or None

            payload_genres = [g["name"] for g in payload.get("genres", []) if g.get("name")]
            if payload_genres:
                owner_genres_json = json.dumps(payload_genres)
            elif genres_hint:
                owner_genres_json = json.dumps([g for g in genres_hint if g])
            else:
                owner_genres_json = None

            conn.execute(
                "INSERT OR IGNORE INTO lib_artists "
                "(id, name, name_lower, source_platform, updated_at) "
                "VALUES (?, ?, ?, 'navidrome', CURRENT_TIMESTAMP)",
                (owner_id, owner_name, _normalize_name(owner_name)),
            )
            conn.execute(
                "UPDATE lib_artists SET name = ?, name_lower = ?, "
                "source_platform = 'navidrome', updated_at = CURRENT_TIMESTAMP, "
                "removed_at = NULL, "
                "thumb_url_navidrome = COALESCE(?, thumb_url_navidrome), "
                "musicbrainz_id = COALESCE(?, musicbrainz_id), "
                "genres_json_navidrome = COALESCE(?, genres_json_navidrome) "
                "WHERE id = ?",
                (owner_name, _normalize_name(owner_name),
                 owner_cover, owner_mbid, owner_genres_json,
                 owner_id),
            )
            conn.execute("INSERT OR IGNORE INTO _seen_artists (id) VALUES (?)", (owner_id,))

        for i, nav_artist in enumerate(all_artists):
            artist_id = _to_id(nav_artist.get("id"))
            if not artist_id:
                logger.debug("navidrome_reader: skipped artist row with empty id: %r", nav_artist)
                continue
            artist_name = nav_artist.get("name", "")
            cover_art = nav_artist.get("coverArt") or None
            mbid = nav_artist.get("musicBrainzId") or None
            genres_list = [g["name"] for g in nav_artist.get("genres", []) if g.get("name")]

            _upsert_artist_row(
                artist_id,
                name_hint=artist_name,
                cover_hint=cover_art,
                mbid_hint=mbid,
                genres_hint=genres_list,
            )
            artist_count += 1

            try:
                artist_detail = client.get_artist(artist_id)
            except Exception as exc:
                logger.warning("navidrome_reader: failed to fetch artist %s: %s", artist_id, exc)
                continue

            for nav_album in artist_detail.get("album", []):
                album_id = _to_id(nav_album.get("id"))
                if not album_id:
                    continue
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
                # Owner precedence for album rows:
                # 1) album payload owner id
                # 2) listing payload owner id
                # 3) outer traversal artist id
                album_owner_id = (
                    _to_id(album_detail.get("artistId"))
                    or _to_id(album_detail.get("albumArtistId"))
                    or _to_id(nav_album.get("artistId"))
                    or _to_id(nav_album.get("albumArtistId"))
                    or artist_id
                )
                album_owner_name = (
                    album_detail.get("artist")
                    or album_detail.get("albumArtist")
                    or nav_album.get("artist")
                    or nav_album.get("albumArtist")
                    or artist_name
                )
                _upsert_artist_row(album_owner_id, name_hint=album_owner_name)

                conn.execute(
                    "INSERT OR IGNORE INTO lib_albums "
                    "(id, artist_id, title, local_title, title_lower, year, "
                    "source_platform, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'navidrome', CURRENT_TIMESTAMP)",
                    (album_id, album_owner_id, album_title, album_title,
                     _normalize_name(album_title), album_year),
                )
                conn.execute(
                    "UPDATE lib_albums SET artist_id = ?, title = ?, local_title = ?, title_lower = ?, "
                    "year = COALESCE(?, year), source_platform = 'navidrome', "
                    "updated_at = CURRENT_TIMESTAMP, removed_at = NULL, "
                    "thumb_url_navidrome = COALESCE(?, thumb_url_navidrome), "
                    "musicbrainz_id = COALESCE(?, musicbrainz_id), "
                    "genres_json_navidrome = COALESCE(?, genres_json_navidrome) "
                    "WHERE id = ?",
                    (album_owner_id, album_title, album_title, _normalize_name(album_title), album_year,
                     album_cover, album_mbid, album_genres_json,
                     album_id),
                )
                conn.execute("INSERT OR IGNORE INTO _seen_albums (id) VALUES (?)", (album_id,))
                album_count += 1

                for song in album_detail.get("song", []):
                    track_id = _to_id(song.get("id"))
                    if not track_id:
                        continue
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
                    # Owner precedence for track rows:
                    # 1) song payload artist id
                    # 2) album owner id
                    # 3) outer traversal artist id
                    track_owner_id = (
                        _to_id(song.get("artistId"))
                        or _to_id(song.get("artist_id"))
                        or album_owner_id
                        or artist_id
                    )
                    track_owner_name = (
                        song.get("artist")
                        or song.get("albumArtist")
                        or album_owner_name
                        or artist_name
                    )
                    # getAlbum() should only return songs for the current album.
                    # Keep album ownership anchored to the current album context.
                    track_album_id = album_id
                    _upsert_artist_row(track_owner_id, name_hint=track_owner_name)

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
                        (track_id, track_album_id, track_owner_id, track_title, track_title.lower(),
                         track_number, disc_number, duration_ms, file_path, file_size,
                         rating, play_count),
                    )
                    # NOTE: rating and play_count are bare assignments (not COALESCE-guarded).
                    # Navidrome is the authoritative source for these values on its own rows —
                    # same pattern as plex_reader.py. If Navidrome returns NULL (unrated), that
                    # is the correct value for this platform's data.
                    conn.execute(
                        "UPDATE lib_tracks SET album_id = ?, artist_id = ?, title = ?, title_lower = ?, "
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
                        (track_album_id, track_owner_id, track_title, track_title.lower(),
                         track_number, disc_number,
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
# Health
# ---------------------------------------------------------------------------

def is_db_accessible() -> bool:
    """Return True if lib_tracks has at least one active navidrome-sourced row."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM lib_tracks WHERE source_platform = 'navidrome' AND removed_at IS NULL"
            ).fetchone()
            return row[0] > 0
    except Exception:
        return False


def get_track_count() -> int:
    """Return total active navidrome track count from lib_tracks."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM lib_tracks WHERE source_platform = 'navidrome' AND removed_at IS NULL"
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def get_native_artist_id(artist_name: str) -> str | None:
    """Return the Navidrome artist ID for an artist by name."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM lib_artists WHERE name_lower = lower(?) "
                "AND source_platform = 'navidrome' LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def get_spotify_artist_id(artist_name: str) -> str | None:
    """Return stored Spotify artist ID for an artist by name."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT spotify_artist_id FROM lib_artists WHERE name_lower = lower(?) LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def get_deezer_artist_id(artist_name: str) -> str | None:
    """Return stored Deezer artist ID for an artist by name."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT deezer_artist_id FROM lib_artists WHERE name_lower = lower(?) LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def get_itunes_artist_id(artist_name: str) -> str | None:
    """Return stored iTunes artist ID for an artist by name."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT itunes_artist_id FROM lib_artists WHERE name_lower = lower(?) LIMIT 1",
                (artist_name,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ownership checks
# ---------------------------------------------------------------------------

def check_album_owned(artist_name: str, album_name: str, *args, **kwargs) -> str | None:
    """Return album ID if the user owns this album, else None."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT la.id FROM lib_albums la "
                "JOIN lib_artists ar ON ar.id = la.artist_id "
                "WHERE ar.name_lower = lower(?) AND la.title_lower = lower(?) "
                "AND la.removed_at IS NULL LIMIT 1",
                (artist_name, album_name),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def check_owned_exact(spotify_track_id: str) -> str | None:
    """Return track ID if this Spotify track ID is in the library."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM lib_tracks WHERE spotify_track_id = ? "
                "AND removed_at IS NULL LIMIT 1",
                (spotify_track_id,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def check_owned_deezer(deezer_track_id: str) -> str | None:
    """Return track ID if this Deezer track ID is in the library."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM lib_tracks WHERE deezer_id = ? "
                "AND removed_at IS NULL LIMIT 1",
                (deezer_track_id,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def find_track_by_name(artist_name: str, track_title: str) -> str | None:
    """Find a track by artist name + track title. Returns track ID or None."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT t.id, t.title, t.album_id FROM lib_tracks t "
                "JOIN lib_artists a ON a.id = t.artist_id "
                "WHERE a.name_lower = lower(?) AND t.title_lower = lower(?) "
                "AND t.removed_at IS NULL LIMIT 1",
                (artist_name, track_title),
            ).fetchone()
            return str(row[0]) if row else None
    except Exception:
        return None


def get_all_tracks_for_artist(artist_id: str) -> list:
    """Return all non-removed tracks for an artist."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id, title, album_id, track_number, duration "
                "FROM lib_tracks WHERE artist_id = ? AND removed_at IS NULL",
                (artist_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_tracks_for_album(artist_id: str, album_title: str) -> list:
    """Return all non-removed tracks for a specific album."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.title, t.track_number, t.duration "
                "FROM lib_tracks t "
                "JOIN lib_albums a ON a.id = t.album_id "
                "WHERE t.artist_id = ? AND a.title_lower = lower(?) "
                "AND t.removed_at IS NULL",
                (artist_id, album_title),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# SoulSync stubs (Plex-only enrichment API)
# ---------------------------------------------------------------------------

def get_discovery_pool(**kwargs) -> list:
    """Not applicable for Navidrome — SoulSync is Plex-only. Returns []."""
    return []


def get_similar_artists_map(**kwargs) -> dict:
    """Not applicable for Navidrome — SoulSync is Plex-only. Returns {}."""
    return {}
