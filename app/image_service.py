"""
image_service.py — Non-blocking image URL resolution via iTunes.

Flow:
  - Cache hit  → return (url, False) immediately (~5ms DB lookup)
  - Cache miss → submit background fetch to thread pool, return ("", True)
                 Flask request thread is NEVER blocked by iTunes calls.

The caller (frontend useImage hook) retries after a short delay when it
gets pending=True, picking up the cached result once the background fetch
completes.

Two iTunes worker threads run concurrently (max), each respecting the 1s
rate limit via _img_lock. This is isolated from the CC pipeline's limiter.
"""
import re
import threading
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor

from app.db import cc_store

logger = logging.getLogger(__name__)

_ITUNES_BASE = "https://itunes.apple.com"
_IMG_RATE = 1.0  # seconds between image iTunes calls (~60/min)
_img_lock = threading.Lock()
_img_last_call = 0.0

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img-worker")

# Tracks keys currently queued/running in the executor — prevents duplicate submissions
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()

_session = requests.Session()
_session.headers["Accept"] = "application/json"
_session.headers["User-Agent"] = "Rythmx/1.0 (music discovery tool)"


def _itunes_img_get(path: str, params: dict) -> dict | None:
    """Rate-limited iTunes GET for image lookups only. Thread-safe."""
    global _img_last_call
    with _img_lock:
        elapsed = time.monotonic() - _img_last_call
        if elapsed < _IMG_RATE:
            time.sleep(_IMG_RATE - elapsed)
        _img_last_call = time.monotonic()
    try:
        resp = _session.get(f"{_ITUNES_BASE}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.debug("Image iTunes request failed: %s", e)
        return None


def _extract_art(data: dict | None) -> str:
    """Pull artworkUrl100 from iTunes response results, upscaled to 600px."""
    if not data:
        return ""
    for item in data.get("results", []):
        raw = item.get("artworkUrl100", "")
        if raw:
            return raw.replace("100x100bb", "600x600bb")
    return ""


def _search_artist_itunes(name: str) -> str | None:
    """Search iTunes for an artist by name, return artistId or None."""
    data = _itunes_img_get("/search", {
        "term": name,
        "entity": "musicArtist",
        "media": "music",
        "limit": 5,
    })
    if not data or not data.get("results"):
        return None
    name_lower = name.lower()
    for artist in data["results"]:
        if artist.get("artistName", "").lower() == name_lower:
            return str(artist["artistId"])
    first = data["results"][0]
    return str(first.get("artistId", "")) or None


def _entity_key(entity_type: str, name: str, artist: str) -> str:
    return name.lower() if entity_type == "artist" else f"{artist.lower()}|||{name.lower()}"


def _fetch_and_cache(entity_type: str, name: str, artist: str) -> None:
    """Background worker: fetch image from iTunes and write to cache."""
    key = _entity_key(entity_type, name, artist)
    try:
        url = ""

        if entity_type == "artist":
            cached_artist = cc_store.get_cached_artist(name)
            itunes_id = (cached_artist or {}).get("itunes_artist_id")

            if not itunes_id:
                itunes_id = _search_artist_itunes(name)
                if itunes_id:
                    cc_store.cache_artist(name, itunes_artist_id=itunes_id)

            if itunes_id:
                data = _itunes_img_get("/lookup", {
                    "id": itunes_id,
                    "entity": "album",
                    "limit": 5,
                })
                url = _extract_art(data)

        elif entity_type == "album":
            # Strip common release-type suffixes so "ICE - Single" searches as "ICE"
            search_name = re.sub(r'\s*[-–]\s*(single|ep|extended play)\s*$', '', name, flags=re.IGNORECASE).strip()
            data = _itunes_img_get("/search", {
                "term": f"{artist} {search_name}",
                "entity": "album",
                "media": "music",
                "limit": 5,
            })
            url = _extract_art(data)

        elif entity_type == "track":
            data = _itunes_img_get("/search", {
                "term": f"{artist} {name}",
                "entity": "song",
                "media": "music",
                "limit": 3,
            })
            url = _extract_art(data)

        if url:
            cc_store.set_image_cache(entity_type, key, url)
        logger.debug("Image cached: [%s] %s — %s", entity_type, name, url[:60] if url else "(none)")
    finally:
        with _in_flight_lock:
            _in_flight.discard(key)


def resolve_image(entity_type: str, name: str, artist: str = "") -> tuple[str, bool]:
    """
    Return (image_url, pending) for an artist / album / track.

    Cache hit  → (url, False)  — instant
    Cache miss → ("", True)    — background fetch submitted; caller retries after delay

    entity_type: 'artist' | 'album' | 'track'
    name:        artist name, album title, or track title
    artist:      artist name (required for album and track lookups)
    """
    key = _entity_key(entity_type, name, artist)

    cached = cc_store.get_image_cache(entity_type, key)
    if cached is not None:
        return cached, False

    with _in_flight_lock:
        if key in _in_flight:
            return "", True  # Already queued — caller retries
        _in_flight.add(key)

    _executor.submit(_fetch_and_cache, entity_type, name, artist)
    return "", True
