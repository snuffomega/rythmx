"""
image_service.py — Non-blocking image URL resolution.

Artist images:  Fanart.tv (real band photos, requires FANART_API_KEY) →
                Deezer /artist/{id} (actual artist photo, free, no auth) →
                iTunes album art last resort (always available, no auth)
Album images:   iTunes search (artworkUrl100, upscaled to 600px)
Track images:   iTunes search (song → album art)

Flow:
  - Cache hit  → return (url, False) immediately (~5ms DB lookup)
  - Cache miss → submit background fetch to thread pool, return ("", True)
                 Flask request thread is NEVER blocked by external calls.

The caller (frontend useImage hook) retries after a short delay when it
gets pending=True, picking up the cached result once the background fetch
completes.

Two worker threads run concurrently (max), each rate-limiting their own
external service calls independently.
"""
import re
import threading
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.db import cc_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# iTunes
# ---------------------------------------------------------------------------

_ITUNES_BASE = "https://itunes.apple.com"
_IMG_RATE = 1.0  # seconds between image iTunes calls (~60/min)
_img_lock = threading.Lock()
_img_last_call = 0.0

# ---------------------------------------------------------------------------
# Fanart.tv
# ---------------------------------------------------------------------------

_FANART_BASE = "https://webservice.fanart.tv/v3/music"
_fanart_lock = threading.Lock()
_fanart_last_call = 0.0

# ---------------------------------------------------------------------------
# Deezer (artist photo lookup — free, no auth, 50 req/sec)
# ---------------------------------------------------------------------------

_DEEZER_BASE = "https://api.deezer.com"
_deezer_img_lock = threading.Lock()
_deezer_img_last_call = 0.0
_DEEZER_IMG_RATE = 0.2  # 5/sec — well within Deezer's 50/sec limit

# ---------------------------------------------------------------------------
# MusicBrainz (for MBID lookups when not in artist_identity_cache)
# ---------------------------------------------------------------------------

_MB_ARTIST_URL = "https://musicbrainz.org/ws/2/artist"
_MB_RATE = 1.1  # seconds between MB calls (their rate limit is 1/sec)
_mb_img_lock = threading.Lock()
_mb_img_last_call = 0.0

# ---------------------------------------------------------------------------
# Thread pool + in-flight tracking
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img-worker")

# Tracks keys currently queued/running in the executor — prevents duplicate submissions
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()

_session = requests.Session()
_session.headers["Accept"] = "application/json"
_session.headers["User-Agent"] = "Rythmx/1.0 (music discovery tool)"


# ---------------------------------------------------------------------------
# iTunes helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fanart.tv helpers
# ---------------------------------------------------------------------------

def _fanart_get_artist(mbid: str) -> str:
    """
    Fetch artist thumbnail from Fanart.tv using a MusicBrainz ID.
    Returns image URL or "" if not found / not configured.
    Rate-limited and thread-safe.
    """
    global _fanart_last_call
    with _fanart_lock:
        elapsed = time.monotonic() - _fanart_last_call
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        _fanart_last_call = time.monotonic()
    try:
        resp = _session.get(
            f"{_FANART_BASE}/{mbid}",
            params={"api_key": config.FANART_API_KEY},
            timeout=10,
        )
        if resp.status_code == 404:
            return ""  # Artist not in Fanart.tv database — normal, not an error
        resp.raise_for_status()
        data = resp.json()
        thumbs = data.get("artistthumb", [])
        if thumbs:
            # Fanart.tv returns thumbs sorted by community likes — first is best
            return thumbs[0].get("url", "")
        return ""
    except requests.RequestException as e:
        logger.debug("Fanart.tv request failed for MBID '%s': %s", mbid, e)
        return ""


# ---------------------------------------------------------------------------
# MusicBrainz MBID lookup (for artists not yet in identity cache)
# ---------------------------------------------------------------------------

def _mb_lookup_mbid(artist_name: str) -> str | None:
    """
    Look up a MusicBrainz artist MBID by name.
    Rate-limited to 1 req/sec (MB policy). Thread-safe.
    Result is cached in artist_identity_cache by the caller.
    """
    global _mb_img_last_call
    with _mb_img_lock:
        elapsed = time.monotonic() - _mb_img_last_call
        if elapsed < _MB_RATE:
            time.sleep(_MB_RATE - elapsed)
        _mb_img_last_call = time.monotonic()
    try:
        resp = _session.get(
            _MB_ARTIST_URL,
            params={"query": f'artist:"{artist_name}"', "limit": 3, "fmt": "json"},
            headers={"User-Agent": "Rythmx/1.0 (https://github.com/snuffomega/rythmx)"},
            timeout=10,
        )
        resp.raise_for_status()
        artists = resp.json().get("artists", [])
        if not artists:
            return None
        name_lower = artist_name.lower()
        for a in artists:
            if a.get("name", "").lower() == name_lower:
                return a["id"]
        return artists[0]["id"]
    except requests.RequestException as e:
        logger.debug("MusicBrainz MBID lookup failed for '%s': %s", artist_name, e)
        return None


# ---------------------------------------------------------------------------
# Deezer helpers
# ---------------------------------------------------------------------------

def _deezer_get_artist_photo(deezer_id: str) -> str:
    """
    Fetch artist photo URL from Deezer /artist/{id}.
    Returns picture_xl (1000px) or picture_big (500px), or "" if none available.
    Rate-limited and thread-safe.
    """
    global _deezer_img_last_call
    with _deezer_img_lock:
        elapsed = time.monotonic() - _deezer_img_last_call
        if elapsed < _DEEZER_IMG_RATE:
            time.sleep(_DEEZER_IMG_RATE - elapsed)
        _deezer_img_last_call = time.monotonic()
    try:
        resp = _session.get(f"{_DEEZER_BASE}/artist/{deezer_id}", timeout=10)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        data = resp.json()
        url = data.get("picture_xl") or data.get("picture_big", "")
        # Deezer uses "/images/artist//" (empty hash) when no photo is available
        if url and "/images/artist//" in url:
            return ""
        return url or ""
    except requests.RequestException as e:
        logger.debug("Deezer artist photo failed for id '%s': %s", deezer_id, e)
        return ""


def _deezer_search_artist_id(name: str) -> str | None:
    """Search Deezer for an artist by name, return artist ID or None."""
    global _deezer_img_last_call
    with _deezer_img_lock:
        elapsed = time.monotonic() - _deezer_img_last_call
        if elapsed < _DEEZER_IMG_RATE:
            time.sleep(_DEEZER_IMG_RATE - elapsed)
        _deezer_img_last_call = time.monotonic()
    try:
        resp = _session.get(
            f"{_DEEZER_BASE}/search/artist",
            params={"q": name, "limit": 5},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            return None
        name_lower = name.lower()
        for item in items:
            if item.get("name", "").lower() == name_lower:
                return str(item["id"])
        return str(items[0]["id"])
    except requests.RequestException as e:
        logger.debug("Deezer artist search failed for '%s': %s", name, e)
        return None


# ---------------------------------------------------------------------------
# Core background worker
# ---------------------------------------------------------------------------

def _entity_key(entity_type: str, name: str, artist: str) -> str:
    return name.lower() if entity_type == "artist" else f"{artist.lower()}|||{name.lower()}"


def _fetch_and_cache(entity_type: str, name: str, artist: str) -> None:
    """Background worker: fetch image from best available source and write to cache."""
    key = _entity_key(entity_type, name, artist)
    try:
        url = ""

        if entity_type == "artist":
            # --- Primary: Fanart.tv (real artist photo) ---
            if config.FANART_API_KEY:
                cached_artist = cc_store.get_cached_artist(name)
                mbid = (cached_artist or {}).get("mb_artist_id")

                if not mbid:
                    mbid = _mb_lookup_mbid(name)
                    if mbid:
                        cc_store.cache_artist(name, mb_artist_id=mbid)

                if mbid:
                    url = _fanart_get_artist(mbid)

            # --- Secondary: Deezer artist photo ---
            if not url:
                cached_artist = cc_store.get_cached_artist(name)
                deezer_id = (cached_artist or {}).get("deezer_artist_id")

                if not deezer_id:
                    deezer_id = _deezer_search_artist_id(name)
                    if deezer_id:
                        cc_store.cache_artist(name, deezer_artist_id=deezer_id)

                if deezer_id:
                    url = _deezer_get_artist_photo(deezer_id)

            # --- Last resort: iTunes album art ---
            if not url:
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def warm_image_cache(max_items: int = 40) -> int:
    """
    Proactively submit background image fetches for entities not yet in cache.
    Called by the scheduler during idle hours (when CC did not run).

    Returns the number of new background fetches submitted (0 = nothing to do).
    Non-blocking — all work runs inside the existing _executor thread pool.
    """
    from app.db import cc_store as _cc_store
    missing = _cc_store.get_missing_image_entities(limit=max_items)
    submitted = 0
    for entity_type, name, artist in missing:
        _, pending = resolve_image(entity_type, name, artist)
        if pending:
            submitted += 1
    if submitted:
        logger.info("Image warmer: submitted %d background fetches", submitted)
    else:
        logger.debug("Image warmer: nothing to fetch")
    return submitted


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
