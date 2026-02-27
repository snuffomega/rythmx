"""
image_service.py — Lazy image URL resolution via iTunes.

Checks image_cache first (instant). On miss, fetches from iTunes and stores.
Uses a dedicated rate limiter (1s between calls) separate from the CC pipeline's
iTunes rate limiter so image fetches don't block cruise control and vice versa.
"""
import threading
import time
import logging
import requests

from app.db import cc_store

logger = logging.getLogger(__name__)

_ITUNES_BASE = "https://itunes.apple.com"
_IMG_RATE = 1.0  # seconds between image iTunes calls (~60/min)
_img_lock = threading.Lock()
_img_last_call = 0.0

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
    # Best-effort: return first result
    first = data["results"][0]
    return str(first.get("artistId", "")) or None


def resolve_image(entity_type: str, name: str, artist: str = "") -> str:
    """
    Return an image URL for an artist / album / track.

    Checks image_cache first — instant on cache hit.
    On miss, fetches from iTunes, stores result, and returns URL.
    Returns "" if nothing found; caller shows gradient fallback.

    entity_type: 'artist' | 'album' | 'track'
    name:        artist name, album title, or track title
    artist:      artist name (required for album and track lookups)
    """
    entity_key = name.lower() if entity_type == "artist" else f"{artist.lower()}|||{name.lower()}"

    # 1. Cache hit
    cached = cc_store.get_image_cache(entity_type, entity_key)
    if cached is not None:
        return cached

    url = ""

    if entity_type == "artist":
        # Fast path: use itunes_artist_id from artist_identity_cache if already resolved
        cached_artist = cc_store.get_cached_artist(name)
        itunes_id = (cached_artist or {}).get("itunes_artist_id")

        if not itunes_id:
            # Resolve via name search and cache the ID for future use
            itunes_id = _search_artist_itunes(name)
            if itunes_id:
                cc_store.cache_artist(name, itunes_artist_id=itunes_id)

        if itunes_id:
            # Lookup artist's albums and grab the first album's artwork
            data = _itunes_img_get("/lookup", {
                "id": itunes_id,
                "entity": "album",
                "limit": 5,
            })
            url = _extract_art(data)

    elif entity_type == "album":
        data = _itunes_img_get("/search", {
            "term": f"{artist} {name}",
            "entity": "album",
            "media": "music",
            "limit": 3,
        })
        url = _extract_art(data)

    elif entity_type == "track":
        data = _itunes_img_get("/search", {
            "term": f"{artist} {name}",
            "entity": "song",
            "media": "music",
            "limit": 3,
        })
        url = _extract_art(data)  # artworkUrl100 on song results = album cover art

    cc_store.set_image_cache(entity_type, entity_key, url)
    return url
