#!/usr/bin/env python3
# app/runners/new_releases.py

from __future__ import annotations

import csv
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pylast
import requests
import spotipy
from plexapi.exceptions import PlexApiException
from plexapi.server import PlexServer
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyClientCredentials

from core.config import CONFIG
from core.logging import setup_logger
from core.state import StateStore


# --- SCRIPT VERSION ---
SCRIPT_VERSION = "New Release Finder v2.25"

# --- CONSTANTS ---
__PLEX_API_ERROR__ = "__PLEX_API_ERROR__"


def _normalize_for_comparison(text: str | None, is_artist: bool = False) -> str:
    if not text:
        return ""
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r" - .*", "", text).strip()
    text = re.sub(r"[\(\[].*?[\)\]]", "", text).strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if is_artist and text.startswith("the "):
        text = text[4:]
    return text


def find_plex_album(plex_music, artist_name: str, album_name: str):
    try:
        results = plex_music.searchAlbums(title=album_name)
        norm_artist = _normalize_for_comparison(artist_name, is_artist=True)
        for album in results:
            if _normalize_for_comparison(album.parentTitle, is_artist=True) == norm_artist:
                return album
        return None
    except (PlexApiException, requests.exceptions.RequestException) as e:
        return __PLEX_API_ERROR__


def find_any_plex_track_version(plex_music, artist_name: str, track_title: str, year: int | None = None):
    try:
        candidates = plex_music.searchTracks(title=track_title)
    except (PlexApiException, requests.exceptions.RequestException):
        return __PLEX_API_ERROR__

    norm_artist = _normalize_for_comparison(artist_name, is_artist=True)
    norm_title = _normalize_for_comparison(track_title)

    for track in candidates:
        plex_year = None
        try:
            album = track.album()
            if hasattr(album, "originallyAvailableAt") and album.originallyAvailableAt:
                plex_year = album.originallyAvailableAt.year
            elif hasattr(album, "year"):
                plex_year = album.year
        except (PlexApiException, requests.exceptions.RequestException):
            continue

        artist_match = _normalize_for_comparison(track.grandparentTitle, is_artist=True) == norm_artist
        title_match = _normalize_for_comparison(track.title) == norm_title
        year_match = year is None or plex_year is None or plex_year == year

        if artist_match and title_match and year_match:
            return track
    return None


def clear_fallback_files(directory: str, prefix: str, logger):
    if not os.path.isdir(directory):
        return
    logger.info(f"Refreshing fallback directory: Deleting old '{prefix}...' files...")
    for filename in os.listdir(directory):
        if filename.startswith(prefix) and filename.endswith(".csv"):
            try:
                os.remove(os.path.join(directory, filename))
            except OSError as e:
                logger.error(f"  - Error deleting file {filename}: {e}")


def save_fallback_playlist(directory: str, all_playlist_tracks: list[dict], logger):
    if not all_playlist_tracks:
        return
    os.makedirs(directory, exist_ok=True)
    playlist_title = f"New Releases - {datetime.now().strftime('%Y-%m-%d')}"
    fallback_filepath = os.path.join(directory, f"{playlist_title}.csv")
    logger.info(f"--- Saving fallback playlist to: {fallback_filepath} ---")

    unique_track_tuples = sorted(list({(t["artist"], t["album"], t["title"]) for t in all_playlist_tracks}))

    try:
        with open(fallback_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Artist", "Album", "Title"])
            writer.writerows(unique_track_tuples)
    except IOError as e:
        logger.error(f"Failed to write fallback playlist file: {e}")


def send_failure_notification(discord_url: str | None, title: str, message: str, logger):
    if not discord_url:
        return
    embed = {
        "title": f"❌ Script Error: {title}",
        "description": message,
        "color": 15158332,
        "footer": {"text": f"Automated by New Release Finder | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
    }
    try:
        requests.post(discord_url, json={"username": "Plex Butler", "embeds": [embed]}, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Discord failure notification: {e}")

def get_new_releases(
    sp,
    network,
    lastfm_username: str,
    min_scrobbles: int,
    days_ago: int,
    lastfm_period: str,
    debug_single_artist: str | None,
    ignore_artists: list[str],
    ignore_keywords: list[str],
    allow_keywords: list[str],
    max_tracks_per_run: int,
    logger,
    state: StateStore,
):
    """
    Pull core artists from Last.fm, then find recent releases on Spotify.

    IMPORTANT CHANGE:
      - We do NOT rely on Spotify search(q='artist:"NAME" year:YYYY', type='album') because it is unreliable.
      - Instead:
          (1) resolve Spotify artist id via artist search
          (2) list releases via sp.artist_albums(artist_id, ...) with pagination
    """

    def _norm(s: str) -> str:
        return _normalize_for_comparison(s or "", True)

    def _resolve_spotify_artist_id(artist_name: str) -> str | None:
        """
        Best-effort resolver for Spotify artist id.
        Prefers exact normalized match; otherwise picks the most popular result.
        """
        try:
            res = sp.search(q=f'artist:"{artist_name}"', type="artist", limit=10)
            items = (res.get("artists") or {}).get("items") or []
            if not items:
                return None

            target = _norm(artist_name)

            # 1) exact normalized match
            for a in items:
                if _norm(a.get("name", "")) == target:
                    return a.get("id")

            # 2) partial containment match
            for a in items:
                if target and target in _norm(a.get("name", "")):
                    return a.get("id")

            # 3) fallback to most popular
            items_sorted = sorted(items, key=lambda x: x.get("popularity", 0), reverse=True)
            return items_sorted[0].get("id")
        except Exception as e:
            logger.warning(f"Spotify artist-id resolve failed for '{artist_name}': {e}")
            return None

    def _iter_artist_albums(artist_id: str):
        """
        Generator yielding album objects from Spotify artist_albums with pagination.
        """
        offset = 0
        limit = 50
        while True:
            page = sp.artist_albums(
                artist_id,
                album_type="album,single",
                limit=limit,
                offset=offset,
            )
            items = page.get("items") or []
            for it in items:
                yield it

            if len(items) < limit:
                break
            offset += limit

    # ---------- Last.fm core artists ----------
    try:
        user = network.get_user(lastfm_username)
        top_artists = user.get_top_artists(period=lastfm_period, limit=1000)
        core_artists = [a.item for a in top_artists if int(a.weight) >= int(min_scrobbles)]

        if debug_single_artist:
            core_artists = [a for a in core_artists if a.name.lower() == debug_single_artist.lower()]

        # helpful sample in logs
        sample = " | ".join([f"'{x.item.name}' pc={x.weight}" for x in top_artists[:3]])
        if sample:
            logger.info(f"Last.fm sample top artists: {sample}")

        logger.info(
            f"Found {len(core_artists)} total core artists "
            f"(period={lastfm_period} min_scrobbles={min_scrobbles} debug_single_artist={debug_single_artist!r})."
        )
    except Exception as e:
        logger.error(f"Failed to fetch artists from Last.fm: {e}")
        return [], []

    # ---------- Spotify releases ----------
    new_releases: list[dict] = []
    skipped_in_search: list[dict] = []
    total_tracks_found = 0

    cutoff_date = datetime.now() - timedelta(days=int(days_ago))
    processed_album_ids: set[str] = set()

    logger.info(f"Checking Spotify for new releases from the last {days_ago} days...")

    for artist in core_artists:
        artist_name = artist.name or ""
        if artist_name in ignore_artists:
            continue

        if max_tracks_per_run > 0 and total_tracks_found >= max_tracks_per_run:
            logger.info("MAX_TRACKS_PER_RUN limit met. Halting search.")
            break

        # tiny throttle
        time.sleep(0.3)

        # Resolve Spotify artist ID once per artist
        spotify_artist_id = _resolve_spotify_artist_id(artist_name)
        if not spotify_artist_id:
            logger.debug(f"Spotify artist id not found for '{artist_name}'. Skipping.")
            continue

        # Walk albums/singles via artist endpoint (reliable)
        try:
            for album in _iter_artist_albums(spotify_artist_id):
                album_id = album.get("id")
                if not album_id:
                    continue

                # avoid repeats within the same run
                if album_id in processed_album_ids:
                    continue
                processed_album_ids.add(album_id)

                main_album_artist = (album.get("artists") or [{}])[0].get("name", "")
                album_name = album.get("name") or ""
                album_name_lower = album_name.lower()

                # strict artist match (keep for now; we’ll improve later)
                if _norm(artist_name) != _norm(main_album_artist):
                    continue

                spotify_url = (album.get("external_urls") or {}).get("spotify")
                album_type = album.get("album_type")
                release_date_str = album.get("release_date")
                release_precision = album.get("release_date_precision")

                # Build meta ONCE so DB/jsonl get consistent data
                release_meta = {
                    "spotify_artist_id": spotify_artist_id,
                    "artist_name": main_album_artist,
                    "album_name": album_name,
                    "release_date": release_date_str,
                    "album_type": album_type,
                    "spotify_url": spotify_url,
                }

                # If already seen, “repair” metadata and skip heavy work
                if state.has_seen_release(album_id):
                    state.mark_release_seen(album_id, release_meta)
                    logger.debug(f"Skipping already-seen album_id={album_id} name='{album_name}'")
                    continue

                # keyword filter
                if any(word in allow_keywords for word in album_name_lower.split()):
                    pass
                elif any(keyword in album_name_lower for keyword in ignore_keywords):
                    skipped_in_search.append(
                        {"artist": main_album_artist, "name": album_name, "reason": "Ignored keyword"}
                    )
                    continue

                # date filter
                if release_precision != "day":
                    continue

                try:
                    release_dt = datetime.strptime(release_date_str or "", "%Y-%m-%d")
                except ValueError:
                    continue

                if release_dt < cutoff_date:
                    continue

                logger.info(f"Found new release from '{main_album_artist}': '{album_name}'")

                # Mark release immediately (so if we crash later it’s still tracked)
                try:
                    state.mark_release_seen(album_id, release_meta)
                except Exception as e:
                    logger.warning(f"Tracking write failed (non-fatal) mark_release_seen: {e}")

                # tracks
                album_tracks_data = sp.album_tracks(album_id)
                track_items = album_tracks_data.get("items") or []
                track_ids = [t.get("id") for t in track_items if t.get("id")]
                if not track_ids:
                    continue

                tracks_details = sp.tracks(track_ids)
                tracks_details_list = tracks_details.get("tracks") or []

                tracks = []
                for t in tracks_details_list:
                    track_id = t.get("id")
                    track_name = t.get("name")
                    isrc = (t.get("external_ids") or {}).get("isrc")

                    tracks.append(
                        {
                            "artist": main_album_artist,
                            "title": track_name,
                            "isrc": isrc,
                            "spotify_track_id": track_id,
                        }
                    )

                    # --- Tracking: mark album seen (non-fatal) ---
                    try:
                        state.mark_release_seen(
                            album_id,
                            {
                                "spotify_artist_id": spotify_artist_id,
                                "artist_name": main_album_artist,
                                "album_name": album_name,
                                "release_date": album.get("release_date"),
                                "album_type": album.get("album_type"),
                                "spotify_url": (album.get("external_urls") or {}).get("spotify"),
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Tracking album write failed (non-fatal): {e}")

                new_releases.append(
                    {
                        "artist": main_album_artist,
                        "name": album_name,
                        "type": album_type,
                        "tracks": tracks,
                        "spotify_album_id": album_id,
                        "spotify_artist_id": spotify_artist_id,
                        "url": spotify_url,
                        "release_date": release_date_str,
                    }
                )

                total_tracks_found += len(tracks)
                if max_tracks_per_run > 0 and total_tracks_found >= max_tracks_per_run:
                    break

        except Exception as e:
            logger.warning(f"Spotify fetch failed for artist '{artist_name}': {e}")
            continue

    return new_releases, skipped_in_search


def main():
    logger, run_id = setup_logger("new_releases")

    # --- Env / service config ---
    PLEX_URL = os.getenv("PLEX_URL")
    PLEX_TOKEN = os.getenv("PLEX_TOKEN")
    PLEX_MUSIC_LIBRARY_NAME = os.getenv("PLEX_MUSIC_LIBRARY_NAME")
    LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
    LASTFM_API_SECRET = os.getenv("LASTFM_API_SECRET")
    LASTFM_USERNAME = os.getenv("LASTFM_USERNAME")
    SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

    # --- Script specific config ---
    LOG_FILE = os.getenv("NEW_MUSIC_LOG_FILE")  # optional legacy
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR")
    STREAMRIP_CONFIG = os.getenv("STREAMRIP_CONFIG")
    BEETS_IMPORT_COMMAND = os.getenv("BEETS_IMPORT_COMMAND")

    script_dir = Path(__file__).resolve().parent
    SPOTIFY_CACHE_FILE = os.getenv("SPOTIFY_CACHE", str(script_dir / "spotify_cache.json"))
    FALLBACK_DIR = str(script_dir / "playlist_fallback")
    STREAMRIP_HOME_DIR = str(script_dir / "streamrip_home")

    # --- Parameters / defaults ---
    dry_run = (os.getenv("RYTHMX_DRY_RUN") or "").lower() in ("1", "true", "yes", "y") or CONFIG["dry_run"]
    debug_single_artist = os.getenv("RYTHMX_DEBUG_SINGLE_ARTIST") or None

    try:
        MIN_SCROBBLES = int(os.getenv("RYTHMX_MIN_SCROBBLES", "17"))
    except ValueError:
        MIN_SCROBBLES = 17

    try:
        DAYS_AGO = int(os.getenv("RYTHMX_DAYS_AGO", "14"))
    except ValueError:
        DAYS_AGO = 14

    LASTFM_PERIOD = os.getenv("RYTHMX_LASTFM_PERIOD", "overall")
    try:
        MAX_TRACKS_PER_RUN = int(os.getenv("RYTHMX_MAX_TRACKS_PER_RUN", "0"))
    except ValueError:
        MAX_TRACKS_PER_RUN = 0

    REPORT_LEVEL = (os.getenv("RYTHMX_REPORT_LEVEL", "skipped") or "skipped").lower()
    PLAYLIST_FALLBACK = (os.getenv("RYTHMX_PLAYLIST_FALLBACK", "1").lower() in ("1", "true", "yes", "y"))
    REFRESH_FALLBACK = (os.getenv("RYTHMX_REFRESH_FALLBACK", "0").lower() in ("1", "true", "yes", "y"))

    IGNORE_KEYWORDS = ["live", "instrumental", "demo"]
    ALLOW_KEYWORDS = ["Sugarshack"]
    RE_RELEASE_KEYWORDS = ["remaster", "deluxe", "remastered", "commentary", "anniversary", "anthology"]
    IGNORE_ARTISTS = ["Academy of St Martin"]

    # --- State ---
    tracking_backend = (os.getenv("TRACKING_BACKEND") or CONFIG["tracking_backend"] or "dual").strip().lower()
    state_dir = os.getenv("STATE_DIR", CONFIG["state_dir"])
    state = StateStore(backend=tracking_backend, state_dir=state_dir)
    logger.info(f"Tracking backend={state.backend} db_enabled={bool(getattr(state, 'db', None))} state_dir={state_dir}")

    logger.info(f"--- Starting Script v{SCRIPT_VERSION} ---")
    logger.info(f"--- Run Mode (effective): Dry Run: {dry_run} ---")
    logger.info(f"Overrides: DEBUG_SINGLE_ARTIST={debug_single_artist} DAYS_AGO={DAYS_AGO} MIN_SCROBBLES={MIN_SCROBBLES}")

    if REFRESH_FALLBACK:
        clear_fallback_files(FALLBACK_DIR, "New Releases", logger)

    # --- Connect services ---
    try:
        os.makedirs(STREAMRIP_HOME_DIR, exist_ok=True)
        lastfm = pylast.LastFMNetwork(api_key=LASTFM_API_KEY, api_secret=LASTFM_API_SECRET)
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIPY_CLIENT_ID,
                client_secret=SPOTIPY_CLIENT_SECRET,
                cache_handler=CacheFileHandler(cache_path=SPOTIFY_CACHE_FILE),
            )
        )
        plex = PlexServer(PLEX_URL, PLEX_TOKEN, timeout=30)
        plex_music = plex.library.section(PLEX_MUSIC_LIBRARY_NAME)
    except Exception as e:
        logger.exception(f"Service Connection Failed: {e}")
        send_failure_notification(
            DISCORD_WEBHOOK_URL,
            "Service Connection Failed",
            f"Could not connect. Error: {e}",
            logger,
        )
        return

    # --- Effective run settings (env overrides) ---
    debug_single_artist = os.getenv("RYTHMX_DEBUG_SINGLE_ARTIST") or DEBUG_SINGLE_ARTIST

    try:
        days_ago = int(os.getenv("RYTHMX_DAYS_AGO", str(DAYS_AGO)))
    except Exception:
        days_ago = DAYS_AGO

    try:
        min_scrobbles = int(os.getenv("RYTHMX_MIN_SCROBBLES", str(MIN_SCROBBLES)))
    except Exception:
        min_scrobbles = MIN_SCROBBLES

    dry_run = os.getenv("RYTHMX_DRY_RUN", "0").lower() in ("1", "true", "yes", "y")

    logger.info(
        f"Overrides: DEBUG_SINGLE_ARTIST={debug_single_artist} "
        f"DAYS_AGO={days_ago} "
        f"MIN_SCROBBLES={min_scrobbles}"
    )

    all_releases, skipped_by_keyword = get_new_releases(
        sp=sp,
        network=lastfm,
        lastfm_username=LASTFM_USERNAME,
        min_scrobbles=min_scrobbles,
        days_ago=days_ago,
        lastfm_period=LASTFM_PERIOD,
        debug_single_artist=debug_single_artist,
        ignore_artists=IGNORE_ARTISTS,
        ignore_keywords=IGNORE_KEYWORDS,
        allow_keywords=ALLOW_KEYWORDS,
        max_tracks_per_run=MAX_TRACKS_PER_RUN,
        logger=logger,
        state=state,
    )
    logger.info(f"get_new_releases returned releases={len(all_releases)} skipped={len(skipped_by_keyword)}")

    albums = [r for r in all_releases if r["type"] == "album"]
    eps = [r for r in all_releases if r["type"] == "single" and len(r["tracks"]) > 1]
    singles = [r for r in all_releases if r["type"] == "single" and len(r["tracks"]) == 1]

    releases_to_download, skipped_releases = [], list(skipped_by_keyword)
    final_playlist_plex_objects, final_playlist_for_fallback = [], []
    queued_track_titles = set()

    # 1. PROCESS ALBUMS & EPs
    logger.info(f"--- Evaluating {len(albums) + len(eps)} new albums & EPs... ---")
    for release in albums + eps:
        if any(_normalize_for_comparison(t["title"]) in queued_track_titles for t in release.get("tracks", [])):
            skipped_releases.append({"artist": release["artist"], "name": release["name"], "reason": "Included in queued album"})
            continue
        if any(keyword in (release["name"] or "").lower() for keyword in RE_RELEASE_KEYWORDS):
            skipped_releases.append({"artist": release["artist"], "name": release["name"], "reason": "Skipped re-release"})
            continue

        plex_album = find_plex_album(plex_music, release["artist"], release["name"])
        if plex_album == __PLEX_API_ERROR__:
            continue

        if plex_album and len(plex_album.tracks()) >= len(release.get("tracks", [])):
            skipped_releases.append({"artist": release["artist"], "name": release["name"], "reason": "Already complete in Plex"})
            final_playlist_plex_objects.extend(plex_album.tracks())
            for t in release["tracks"]:
                final_playlist_for_fallback.append({"artist": t["artist"], "album": release["name"], "title": t["title"]})
            continue

        release_year = int((release.get("release_date") or "0").split("-")[0] or 0) or None
        all_tracks_exist = True
        plex_track_matches = []
        for track in release["tracks"]:
            match = find_any_plex_track_version(plex_music, track["artist"], track["title"], year=release_year)
            if match == __PLEX_API_ERROR__ or not match:
                all_tracks_exist = False
                break
            plex_track_matches.append(match)

        if all_tracks_exist:
            skipped_releases.append({"artist": release["artist"], "name": release["name"], "reason": "All tracks already in Plex"})
            final_playlist_plex_objects.extend(plex_track_matches)
            for t in release["tracks"]:
                final_playlist_for_fallback.append({"artist": t["artist"], "album": release["name"], "title": t["title"]})
            continue

        releases_to_download.append(release)
        for track in release["tracks"]:
            queued_track_titles.add(_normalize_for_comparison(track["title"]))

    # 2. PROCESS SINGLES
    logger.info(f"--- Evaluating {len(singles)} new singles... ---")
    for release in singles:
        track = release["tracks"][0]
        if _normalize_for_comparison(track["title"]) in queued_track_titles:
            skipped_releases.append({"artist": track["artist"], "name": track["title"], "reason": "Included in queued album/EP"})
            continue

        release_year = int((release.get("release_date") or "0").split("-")[0] or 0) or None
        match = find_any_plex_track_version(plex_music, track["artist"], track["title"], year=release_year)
        if match == __PLEX_API_ERROR__:
            continue
        if match:
            skipped_releases.append({"artist": track["artist"], "name": track["title"], "reason": "Already exists in Plex"})
            final_playlist_plex_objects.append(match)
            final_playlist_for_fallback.append({"artist": track["artist"], "album": release["name"], "title": track["title"]})
            continue

        releases_to_download.append(release)

    logger.info(f"Final download queue has {len(releases_to_download)} releases.")

    if dry_run:
        for r in releases_to_download:
            logger.info(f"DRY_RUN would download: {r['artist']} - {r['name']}")
        if PLAYLIST_FALLBACK and final_playlist_for_fallback:
            save_fallback_playlist(FALLBACK_DIR, final_playlist_for_fallback, logger)
        logger.info("--- Dry run complete ---")
        return

    # If you want: keep your existing download/beets/plex playlist logic below.
    # For now, we exit cleanly because you were focused on discovery + tracking consistency.
    logger.info("--- Non-dry run path not executed in this template. Hook your download/import flow here. ---")


if __name__ == "__main__":
    main()
