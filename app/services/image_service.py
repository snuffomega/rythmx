"""
image_service.py — Non-blocking image URL resolution.

Artist images:  Fanart.tv (real band photos, requires FANART_API_KEY) →
                Last.fm artist.getInfo image (requires LASTFM_API_KEY) →
                Discogs artist image (public API, optional token) →
                Deezer /artist/{id} (actual artist photo, free, no auth) →
                iTunes album art last resort (always available, no auth)
Album images:   iTunes search (artworkUrl100, upscaled to 600px)
Track images:   iTunes search (song → album art)

Three-tier cache (L1 → L2 → L3):
  L1  In-memory dict   — ~0ms, 5-min TTL, capped at 2 000 entries
  L2  SQLite image_cache — ~1ms, 30-day eviction (pruned at startup)
  L3  API fetch          — ~200-500ms, runs in background thread pool

  - Cache hit  → return (url, False) immediately (L1 or L2)
  - Cache miss → submit background fetch to L3 thread pool, return ("", True)
                 Request thread is NEVER blocked by external calls.

The caller (frontend useImage hook) retries after a short delay when it
gets pending=True, picking up the cached result once the background fetch
completes.

Two worker threads run concurrently (max), each rate-limiting their own
external service calls independently.
"""
import re
import time
import threading
import logging
import difflib
import requests
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.db import rythmx_store
from app.services import artwork_store
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# iTunes
# ---------------------------------------------------------------------------

_ITUNES_BASE = "https://itunes.apple.com"

# ---------------------------------------------------------------------------
# Fanart.tv
# ---------------------------------------------------------------------------

_FANART_BASE = "https://webservice.fanart.tv/v3/music"

# ---------------------------------------------------------------------------
# Deezer (artist photo lookup — free, no auth)
# ---------------------------------------------------------------------------

_DEEZER_BASE = "https://api.deezer.com"

# ---------------------------------------------------------------------------
# Discogs (artist image lookup — public API, optional token)
# ---------------------------------------------------------------------------

_DISCOGS_BASE = "https://api.discogs.com"

# ---------------------------------------------------------------------------
# Navidrome (coverArt — served by the Navidrome server with Subsonic auth)
# ---------------------------------------------------------------------------

def _navidrome_cover_art_url(cover_art_id: str) -> str:
    """
    Construct a Navidrome /getCoverArt URL with token auth baked in.
    Returns "" if Navidrome is not configured.
    The URL is safe to embed in <img src> — auth is in the query params.
    """
    import hashlib
    import secrets as _secrets
    from app.db import rythmx_store

    url = rythmx_store.get_setting("navidrome_url") or config.NAVIDROME_URL
    user = rythmx_store.get_setting("navidrome_user") or config.NAVIDROME_USER
    password = rythmx_store.get_setting("navidrome_pass") or config.NAVIDROME_PASS

    if not url or not user or not password:
        return ""

    salt = _secrets.token_hex(8)
    token = hashlib.md5((password + salt).encode()).hexdigest()
    base = url.rstrip("/")
    return (
        f"{base}/rest/getCoverArt"
        f"?id={cover_art_id}&u={user}&t={token}&s={salt}"
        f"&v=1.16.1&c=rythmx&f=json"
    )


# ---------------------------------------------------------------------------
# MusicBrainz (for MBID lookups when not in artist_identity_cache)
# ---------------------------------------------------------------------------

_MB_ARTIST_URL = "https://musicbrainz.org/ws/2/artist"

# ---------------------------------------------------------------------------
# Thread pool + in-flight tracking
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img-worker")

# Tracks keys currently queued/running in the executor — prevents duplicate submissions
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()

# ---------------------------------------------------------------------------
# L1: In-memory cache — avoids SQLite round-trip for hot entities (~0ms)
# ---------------------------------------------------------------------------

_mem_cache: dict[str, tuple[str, float]] = {}  # key → (url, timestamp)
_MEM_TTL = 300  # seconds — entries expire after 5 minutes
_MEM_MAX = 2000  # cap to prevent unbounded growth; LRU-style eviction on overflow


def _mem_cache_put(key: str, url: str, ts: float) -> None:
    """Write to L1 cache with size cap enforcement."""
    _mem_cache[key] = (url, ts)
    if len(_mem_cache) > _MEM_MAX:
        oldest_key = min(_mem_cache, key=lambda k: _mem_cache[k][1])
        _mem_cache.pop(oldest_key, None)

_session = requests.Session()
_session.headers["Accept"] = "application/json"
_session.headers["User-Agent"] = "Rythmx/1.0 (music discovery tool)"


# ---------------------------------------------------------------------------
# iTunes helpers
# ---------------------------------------------------------------------------

def _itunes_img_get(path: str, params: dict) -> dict | None:
    """Rate-limited iTunes GET for image lookups. Thread-safe via DomainRateLimiter."""
    rate_limiter.acquire("itunes")
    try:
        resp = _session.get(f"{_ITUNES_BASE}{path}", params=params, timeout=10)
        if resp.status_code == 429:
            rate_limiter.record_429("itunes")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("itunes")
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


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (value or "").lower())).strip()


def _norm_album_title(value: str) -> str:
    base = re.sub(r"\s*[-–]\s*(single|ep|extended play)\s*$", "", value or "", flags=re.IGNORECASE).strip()
    return _norm_text(base)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _select_itunes_album_art(data: dict | None, artist: str, album_title: str) -> str:
    """
    Pick album art from iTunes /search results only when both artist and title
    are reasonably similar to the requested pair.
    """
    if not data:
        return ""

    target_artist = _norm_text(artist)
    target_title = _norm_album_title(album_title)
    best_url = ""
    best_rank = 0.0

    for item in data.get("results", []):
        raw = item.get("artworkUrl100", "")
        if not raw:
            continue

        candidate_title = _norm_album_title(item.get("collectionName", "") or item.get("trackName", ""))
        candidate_artist = _norm_text(item.get("artistName", ""))

        title_score = _similarity(target_title, candidate_title)
        artist_score = _similarity(target_artist, candidate_artist)

        # Guard against cross-artist false positives.
        if title_score < 0.82 or artist_score < 0.72:
            continue

        rank = (title_score * 0.65) + (artist_score * 0.35)
        if rank > best_rank:
            best_rank = rank
            best_url = raw

    return best_url.replace("100x100bb", "600x600bb") if best_url else ""


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

def fanart_get_artist(mbid: str) -> str:
    """
    Fetch artist thumbnail from Fanart.tv using a MusicBrainz ID.
    Returns image URL or "" if not found / not configured.
    Rate-limited via DomainRateLimiter. Thread-safe.
    """
    rate_limiter.acquire("fanart")
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
    Rate-limited to 1 req/sec (MB policy) via DomainRateLimiter. Thread-safe.
    Result is cached in artist_identity_cache by the caller.
    """
    rate_limiter.acquire("musicbrainz")
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

def deezer_get_artist_photo(deezer_id: str) -> str:
    """
    Fetch artist photo URL from Deezer /artist/{id}.
    Returns picture_xl (1000px) or picture_big (500px), or "" if none available.
    Rate-limited via DomainRateLimiter. Thread-safe.
    """
    rate_limiter.acquire("deezer")
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


def _deezer_search_album_art(artist: str, album_title: str) -> str:
    """
    Search Deezer for an album cover by artist + album title.
    Returns cover_xl (1000px) or cover_medium (250px), or "" if not found.
    Rate-limited via DomainRateLimiter. Thread-safe.
    """
    rate_limiter.acquire("deezer")
    try:
        resp = _session.get(
            f"{_DEEZER_BASE}/search/album",
            params={"q": f"{artist} {album_title}", "limit": 5},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        target_artist = _norm_text(artist)
        target_title = _norm_album_title(album_title)
        best_url = ""
        best_rank = 0.0
        for item in items:
            url = item.get("cover_xl") or item.get("cover_medium", "")
            if not url or "/images/cover//" in url:
                continue
            candidate_artist = _norm_text((item.get("artist") or {}).get("name", ""))
            candidate_title = _norm_album_title(item.get("title", ""))
            title_score = _similarity(target_title, candidate_title)
            artist_score = _similarity(target_artist, candidate_artist)
            if title_score < 0.82 or artist_score < 0.72:
                continue
            rank = (title_score * 0.65) + (artist_score * 0.35)
            if rank > best_rank:
                best_rank = rank
                best_url = url
        return best_url
    except requests.RequestException as e:
        logger.debug("Deezer album art search failed for '%s - %s': %s", artist, album_title, e)
        return ""


def _deezer_search_artist_id(name: str) -> str | None:
    """Search Deezer for an artist by name, return artist ID or None."""
    rate_limiter.acquire("deezer")
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
# Discogs helpers
# ---------------------------------------------------------------------------

def _discogs_headers() -> dict[str, str]:
    headers = {"User-Agent": "Rythmx/1.0 (https://github.com/snuffomega/rythmx)"}
    token = (config.DISCOGS_TOKEN or "").strip()
    if token:
        headers["Authorization"] = f"Discogs token={token}"
    return headers


def _discogs_get(path: str, params: dict | None = None) -> dict | None:
    rate_limiter.acquire("discogs")
    try:
        resp = _session.get(
            f"{_DISCOGS_BASE}{path}",
            params=params or {},
            headers=_discogs_headers(),
            timeout=10,
        )
        if resp.status_code == 429:
            rate_limiter.record_429("discogs")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("discogs")
        return resp.json()
    except requests.RequestException as e:
        logger.debug("Discogs request failed for '%s': %s", path, e)
        return None


def _discogs_search_artist_id(name: str) -> str | None:
    """Search Discogs for artist ID by name."""
    data = _discogs_get(
        "/database/search",
        params={"q": name, "type": "artist", "per_page": 5},
    )
    if not data:
        return None

    items = data.get("results", [])
    if not items:
        return None

    target = _norm_text(name)
    for item in items:
        title = re.sub(r"\s+\(\d+\)$", "", str(item.get("title", ""))).strip()
        if _norm_text(title) == target:
            return str(item.get("id", "")) or None

    return str(items[0].get("id", "")) or None


def discogs_get_artist_photo(artist_id: str) -> str:
    """Fetch primary artist image URL from Discogs /artists/{id}."""
    if not artist_id:
        return ""

    data = _discogs_get(f"/artists/{artist_id}")
    if not data:
        return ""

    images = data.get("images") or []
    if not isinstance(images, list) or not images:
        return ""

    primary = next((img for img in images if img.get("type") == "primary"), None)
    candidate = primary or images[0]
    if not isinstance(candidate, dict):
        return ""
    return str(candidate.get("uri") or candidate.get("uri150") or "").strip()


def discogs_get_artist_photo_by_name(name: str) -> str:
    """Search Discogs by artist name, then fetch the artist's primary image."""
    artist_id = _discogs_search_artist_id(name)
    if not artist_id:
        return ""
    return discogs_get_artist_photo(artist_id)


# ---------------------------------------------------------------------------
# Core background worker
# ---------------------------------------------------------------------------

def _entity_key(entity_type: str, name: str, artist: str) -> str:
    return name.lower() if entity_type == "artist" else f"{artist.lower()}|||{name.lower()}"


def _entity_keys(entity_type: str, name: str, artist: str) -> tuple[list[str], int | None]:
    """
    Return candidate cache keys ordered by preference plus match_confidence.

    Preference:
      1. Stable DB id key (when resolvable)
      2. Legacy normalized name key
    """
    legacy_key = _entity_key(entity_type, name, artist)
    keys: list[str] = []
    confidence: int | None = None

    try:
        with rythmx_store._connect() as conn:
            if entity_type == "artist":
                row = conn.execute(
                    """
                    SELECT id, match_confidence
                    FROM lib_artists
                    WHERE removed_at IS NULL
                      AND lower(name) = lower(?)
                    LIMIT 1
                    """,
                    (name,),
                ).fetchone()
                if row:
                    keys.append(str(row["id"]))
                    confidence = int(row["match_confidence"] or 0)
            elif entity_type == "album":
                row = conn.execute(
                    """
                    SELECT al.id, al.match_confidence
                    FROM lib_albums al
                    JOIN lib_artists ar ON ar.id = al.artist_id
                    WHERE al.removed_at IS NULL
                      AND ar.removed_at IS NULL
                      AND lower(ar.name) = lower(?)
                      AND lower(al.title) = lower(?)
                    LIMIT 1
                    """,
                    (artist, name),
                ).fetchone()
                if row:
                    keys.append(str(row["id"]))
                    confidence = int(row["match_confidence"] or 0)
    except Exception as exc:
        logger.debug("image key resolve failed (%s/%s): %s", entity_type, name, exc)

    if legacy_key not in keys:
        keys.append(legacy_key)
    return keys, confidence


def _cache_not_found(entity_type: str, cache_keys: list[str]) -> None:
    """Persist a not-found marker so repeated resolve attempts do not keep re-queueing."""
    for key in cache_keys:
        rythmx_store.set_image_cache_entry(
            entity_type,
            key,
            "",
            local_path=None,
            content_hash=None,
            artwork_source="not_found",
        )


def _fetch_and_cache(
    entity_type: str,
    name: str,
    artist: str,
    cache_keys: list[str],
    in_flight_key: str,
) -> None:
    """Background worker: fetch image from best available source and write to cache."""
    try:
        url = ""

        if entity_type == "artist":
            # --- Navidrome primary: coverArt from lib_artists (when platform=navidrome) ---
            platform = config.LIBRARY_PLATFORM
            mbid = ""
            try:
                platform = rythmx_store.get_setting("library_platform") or platform
            except Exception:
                pass

            if platform == "navidrome":
                try:
                    cover_art_id = rythmx_store.get_artist_navidrome_cover(name)
                    if cover_art_id:
                        url = _navidrome_cover_art_url(cover_art_id)
                except Exception:
                    pass

            # --- Primary: Fanart.tv (real artist photo) ---
            if config.FANART_API_KEY:
                cached_artist = rythmx_store.get_cached_artist(name)
                mbid = (cached_artist or {}).get("mb_artist_id")

                if not mbid:
                    mbid = _mb_lookup_mbid(name)
                    if mbid:
                        rythmx_store.cache_artist(name, mb_artist_id=mbid)

                if mbid:
                    url = fanart_get_artist(mbid)

            # --- Secondary: Last.fm artist photo ---
            if not url and config.LASTFM_API_KEY:
                try:
                    from app.clients.last_fm_client import get_artist_image_lastfm
                    url = get_artist_image_lastfm(mbid=mbid, name=name)
                except Exception as exc:
                    logger.debug("Last.fm artist image lookup failed for '%s': %s", name, exc)

            # --- Tertiary: Discogs artist photo ---
            if not url:
                url = discogs_get_artist_photo_by_name(name)

            # --- Quaternary: Deezer artist photo ---
            if not url:
                cached_artist = rythmx_store.get_cached_artist(name)
                deezer_id = (cached_artist or {}).get("deezer_artist_id")

                if not deezer_id:
                    deezer_id = _deezer_search_artist_id(name)
                    if deezer_id:
                        rythmx_store.cache_artist(name, deezer_artist_id=deezer_id)

                if deezer_id:
                    url = deezer_get_artist_photo(deezer_id)

            # --- Last resort: iTunes album art ---
            if not url:
                cached_artist = rythmx_store.get_cached_artist(name)
                itunes_id = (cached_artist or {}).get("itunes_artist_id")

                if not itunes_id:
                    itunes_id = _search_artist_itunes(name)
                    if itunes_id:
                        rythmx_store.cache_artist(name, itunes_artist_id=itunes_id)

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

            # Tier 0: Direct iTunes /lookup when itunes_album_id is known in lib_releases
            itunes_album_id = rythmx_store.get_release_itunes_album_id(artist, name)
            if itunes_album_id:
                data = _itunes_img_get("/lookup", {
                    "id": itunes_album_id,
                    "entity": "collection",
                    "limit": 1,
                })
                url = _extract_art(data)

            # Tier 1: iTunes name search
            if not url:
                data = _itunes_img_get("/search", {
                    "term": f"{artist} {search_name}",
                    "entity": "album",
                    "media": "music",
                    "limit": 5,
                })
                url = _select_itunes_album_art(data, artist, search_name)

            # Tier 1b: Retry with punctuation-stripped artist name ("Ballyhoo!" → "ballyhoo")
            if not url:
                clean_artist = re.sub(r"[^\w\s]", " ", artist).strip()
                if clean_artist.lower() != artist.lower():
                    data = _itunes_img_get("/search", {
                        "term": f"{clean_artist} {search_name}",
                        "entity": "album",
                        "media": "music",
                        "limit": 5,
                    })
                    url = _select_itunes_album_art(data, artist, search_name)

            # Tier 2: Deezer album search
            if not url:
                url = _deezer_search_album_art(artist, search_name)

        elif entity_type == "track":
            data = _itunes_img_get("/search", {
                "term": f"{artist} {name}",
                "entity": "song",
                "media": "music",
                "limit": 3,
            })
            url = _extract_art(data)

        if url:
            content_hash: str | None = None
            local_path: str | None = None
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                if resp.content:
                    content_hash = artwork_store.ingest(resp.content)
                    local_path = str(artwork_store.get_original_path(content_hash))
            except Exception as exc:
                logger.warning("L3 ingest failed for %s/%s: %s", entity_type, name, exc)

            for key in cache_keys:
                if content_hash:
                    rythmx_store.set_image_cache_entry(
                        entity_type,
                        key,
                        url,
                        local_path=local_path,
                        content_hash=content_hash,
                        artwork_source="runtime",
                    )
                else:
                    rythmx_store.set_image_cache(entity_type, key, url)
                _mem_cache_put(key, url, time.time())
            logger.debug("Image cached: [%s] %s -> %s", entity_type, name, url[:60])
        else:
            _cache_not_found(entity_type, cache_keys)
            logger.info("Image search not found: type=%s artist='%s' name='%s'", entity_type, artist, name)
    finally:
        with _in_flight_lock:
            _in_flight.discard(in_flight_key)


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
    from app.db import rythmx_store
    missing = rythmx_store.get_missing_image_entities(limit=max_items)
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

    Tiered cache: L1 memory (~0ms) → L2 SQLite (~1ms) → L3 API fetch (async, ~200-500ms).

    Cache hit  → (url, False)  — instant
    Cache miss → ("", True)    — background fetch submitted; caller retries after delay

    entity_type: 'artist' | 'album' | 'track'
    name:        artist name, album title, or track title
    artist:      artist name (required for album and track lookups)
    """
    keys, confidence = _entity_keys(entity_type, name, artist)
    inflight_key = keys[0]
    now = time.time()

    # --- L1: in-memory dict (instant, ~0ms), check all alias keys ---
    for key in keys:
        entry = _mem_cache.get(key)
        if entry and (now - entry[1]) < _MEM_TTL:
            return entry[0], False

    # --- L2: SQLite image_cache (~1ms), check all alias keys ---
    for key in keys:
        row = rythmx_store.get_image_cache_entry(entity_type, key) or {}
        cached_url = str(row.get("image_url") or "").strip()
        if cached_url:
            for alias in keys:
                _mem_cache_put(alias, cached_url, now)
            return cached_url, False
        if str(row.get("artwork_source") or "") == "not_found":
            return "", False

    # Gate live lookups to higher-confidence metadata only.
    if entity_type in ("artist", "album"):
        if confidence is None or confidence < 85:
            logger.debug(
                "resolve_image: skip live lookup for low-confidence %s '%s' (artist='%s', confidence=%s)",
                entity_type, name, artist, confidence,
            )
            return "", False

    # --- L3: API fetch (background thread, ~200-500ms) ---
    with _in_flight_lock:
        if inflight_key in _in_flight:
            return "", True  # Already queued — caller retries
        _in_flight.add(inflight_key)

    _executor.submit(_fetch_and_cache, entity_type, name, artist, keys, inflight_key)
    return "", True
