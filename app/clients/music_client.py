"""
music_client.py — unified music catalog client for new release discovery.

Provider priority (MUSIC_API_PROVIDER=auto):
  Spotify (if SPOTIFY_CLIENT_ID/SECRET configured) → iTunes → Deezer

iTunes is the primary free provider:
  - SoulSync already enriches artists.itunes_artist_id, so most searches are skipped
  - itunes_album_id enables exact owned-check against albums.itunes_album_id in SoulSync DB
  - 20 req/min rate limit is comfortable for batched background runs

MusicBrainz is NOT in the auto chain — it requires 1 req/sec and connection-resets
from Docker networking. Set MUSIC_API_PROVIDER=musicbrainz to use it explicitly.

Used by Cruise Control pipeline for artist resolution and new release discovery.
No DB access — pure API calls. Caller is responsible for caching via rythmx_store.
"""
import unicodedata
import re
import time
import logging
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
from app import config
from app.services.api_orchestrator import rate_limiter

logger = logging.getLogger(__name__)

_ARTICLES = frozenset({"the", "a", "an"})
_ITUNES_BASE = "https://itunes.apple.com"
_DEEZER_BASE = "https://api.deezer.com"
_MB_BASE = "https://musicbrainz.org/ws/2"
_MB_USER_AGENT = "rythmx/1.0 (https://github.com/snuffomega/rythmx)"
_MB_RATE_INTERVAL = 1.1  # seconds between MusicBrainz requests


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    """
    Normalize a string for cross-service artist/album matching.
    Ported from rythmx_cli: NFKC unicode + lowercase + strip leading articles + remove punctuation.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    words = s.split()
    if words and words[0] in _ARTICLES:
        words = words[1:]
    s = " ".join(words)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class Release:
    artist: str
    title: str
    release_date: str       # YYYY-MM-DD
    kind: str               # album / single / ep / compile
    source: str             # itunes / deezer / spotify / musicbrainz
    source_url: str = ""
    deezer_album_id: str = ""
    spotify_album_id: str = ""
    itunes_album_id: str = ""
    artwork_url: str = ""       # Album art URL (iTunes artworkUrl100 or equivalent)
    is_upcoming: bool = False   # True if release_date > today at fetch time (pre-announcement)


# ---------------------------------------------------------------------------
# iTunes Search API (no auth, 20 req/min)
# ---------------------------------------------------------------------------

_itunes_session = requests.Session()
_itunes_session.headers["Accept"] = "application/json"
_itunes_session.headers["User-Agent"] = _MB_USER_AGENT


def _itunes_get(path: str, params: dict = None) -> dict | None:
    rate_limiter.acquire("itunes")
    try:
        resp = _itunes_session.get(f"{_ITUNES_BASE}{path}", params=params, timeout=15)
        if resp.status_code == 429:
            rate_limiter.record_429("itunes")
            return None
        resp.raise_for_status()
        rate_limiter.record_success("itunes")
        return resp.json()
    except requests.RequestException as e:
        logger.error("iTunes request failed (%s): %s", path, e)
        return None


def _search_variants(name: str) -> list[str]:
    """
    Return a list of search term variants to try for a given artist name.
    First item is always the original; subsequent items are cleaned versions
    for names that contain special characters (e.g. "Hall & Oates").
    """
    variants = [name]
    cleaned = name.replace("&", "and").replace("+", "and")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned != name:
        variants.append(cleaned)
    return variants


def _itunes_search_artist(name: str) -> str | None:
    """Return best-match iTunes artistId for a given name, or None.

    Tries the original name first; if that returns no results, retries with
    a cleaned variant (& → and, punctuation stripped) so "Hall & Oates" still
    resolves correctly.
    """
    norm_name = norm(name)
    for term in _search_variants(name):
        data = _itunes_get("/search", {
            "term": term,
            "entity": "musicArtist",
            "media": "music",
            "limit": 5,
        })
        if not data or not data.get("results"):
            continue
        for artist in data["results"]:
            if norm(artist.get("artistName", "")) == norm_name:
                return str(artist["artistId"])
        return str(data["results"][0]["artistId"])
    return None


def _itunes_get_releases(itunes_artist_id: str, cutoff: datetime,
                         allowed_kinds: set, ignore_kw: list[str]) -> list[Release]:
    """Return new releases for an iTunes artist ID since cutoff."""
    data = _itunes_get("/lookup", {
        "id": itunes_artist_id,
        "entity": "album",
        "limit": 200,
    })
    if not data or not data.get("results"):
        return []

    releases = []
    for item in data["results"]:
        if item.get("wrapperType") != "collection":
            continue
        date_str = item.get("releaseDate", "")
        if not date_str:
            continue
        # iTunes returns ISO 8601: "2025-03-14T07:00:00Z"
        try:
            rel_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if rel_date < cutoff:
            continue
        is_upcoming = rel_date > datetime.utcnow()
        title = item.get("collectionName", "")
        if any(kw in title.lower() for kw in ignore_kw):
            continue
        # iTunes collectionType is usually "Album"; detect singles/EPs from title suffix
        kind = item.get("collectionType", "album").lower()
        if kind == "album":
            lower_title = title.lower()
            if "- single" in lower_title or lower_title.endswith(" single"):
                kind = "single"
            elif " - ep" in lower_title or lower_title.endswith(" ep"):
                kind = "ep"
        if kind not in allowed_kinds:
            if "album" in allowed_kinds:
                kind = "album"  # accept as album when exact kind is ambiguous
            else:
                continue
        # iTunes returns artworkUrl100 — upgrade to 600px for better quality
        raw_art = item.get("artworkUrl100", "")
        artwork = raw_art.replace("100x100bb", "600x600bb") if raw_art else ""
        releases.append(Release(
            artist=item.get("artistName", ""),
            title=title,
            release_date=date_str[:10],
            kind=kind,
            source="itunes",
            source_url=item.get("collectionViewUrl", ""),
            itunes_album_id=str(item.get("collectionId", "")),
            artwork_url=artwork,
            is_upcoming=is_upcoming,
        ))
    return releases


def search_artist_candidates_itunes(name: str, limit: int = 5) -> list[dict]:
    """
    Return iTunes artist search candidates as [{name: str, id: str}].
    Tries name variants (& → and, punctuation stripped) if the original returns nothing.
    Used by identity_resolver to score candidates before fetching top tracks.
    """
    results = []
    for term in _search_variants(name):
        data = _itunes_get("/search", {
            "term": term,
            "entity": "musicArtist",
            "media": "music",
            "limit": limit,
        })
        if not data or not data.get("results"):
            continue
        for artist in data["results"]:
            aid = str(artist.get("artistId", ""))
            aname = artist.get("artistName", "")
            if aid:
                results.append({"name": aname, "id": aid})
        if results:
            break
    return results


def _derive_collection_type(item: dict) -> str:
    """Derive album/single/ep from iTunes collectionType + title suffix."""
    kind = (item.get("collectionType") or "Album").lower()
    if kind == "album":
        title = (item.get("collectionName") or "").lower()
        if "- single" in title or title.endswith(" single"):
            return "single"
        if " - ep" in title or title.endswith(" ep"):
            return "ep"
    return kind


def get_artist_albums_itunes(itunes_artist_id: str) -> list[dict]:
    """
    Return the full album catalog for an iTunes artist ID.
    Returns up to 200 albums as [{id, title, track_count, record_type}].
    Used by validate_artist() to score album-catalog overlap and by
    enrich_library() for album matching with track-count tiebreakers.
    """
    data = _itunes_get("/lookup", {
        "id": itunes_artist_id,
        "entity": "album",
        "limit": 200,
    })
    if not data or not data.get("results"):
        return []
    results = []
    for item in data["results"]:
        if item.get("wrapperType") != "collection" or not item.get("collectionName"):
            continue
        raw_art = item.get("artworkUrl100", "")
        results.append({
            "id": str(item.get("collectionId", "")),
            "title": item.get("collectionName", ""),
            "track_count": item.get("trackCount") or 0,
            "record_type": _derive_collection_type(item),
            "artwork_url": raw_art.replace("100x100bb", "600x600bb") if raw_art else "",
            "release_date": item.get("releaseDate", ""),
            "explicit": item.get("collectionExplicitness", "") == "explicit",
            "label": item.get("copyright", ""),
            "genre": item.get("primaryGenreName", ""),
        })
    return results


def get_artist_top_tracks_itunes(itunes_artist_id: str, limit: int = 10) -> list[str]:
    """
    Return top track title strings for an iTunes artist ID.
    Uses /lookup with entity=song to get the artist's top tracks.
    Returns raw (un-normalized) title strings — caller normalizes.
    Returns empty list on error.
    """
    data = _itunes_get("/lookup", {
        "id": itunes_artist_id,
        "entity": "song",
        "attribute": "artistTerm",
        "limit": limit + 1,  # +1 because first result is the artist object itself
    })
    if not data or not data.get("results"):
        return []
    titles = [
        r.get("trackName", "")
        for r in data["results"]
        if r.get("wrapperType") == "track" and r.get("trackName")
    ]
    logger.debug("iTunes top tracks for ID %s: %d", itunes_artist_id, len(titles))
    return titles[:limit]


# ---------------------------------------------------------------------------
# Deezer (no auth required)
# ---------------------------------------------------------------------------

_deezer_session = requests.Session()
_deezer_session.headers["Accept"] = "application/json"


def get_album_itunes_rich(itunes_album_id: str) -> dict | None:
    """
    Fetch genre and release_date for an album from iTunes by collectionId.
    Returns {genre: str, release_date: str} or None if not found / API error.
    release_date is trimmed to YYYY-MM-DD format.
    Used by enrich_itunes_rich() (Stage 3 S3-1).
    """
    data = _itunes_get("/lookup", {"id": itunes_album_id, "entity": "album"})
    if not data or not data.get("results"):
        return None
    for item in data["results"]:
        if (item.get("wrapperType") == "collection"
                and str(item.get("collectionId", "")) == str(itunes_album_id)):
            return {
                "genre": item.get("primaryGenreName", "") or "",
                "release_date": (item.get("releaseDate", "") or "")[:10],
            }
    # Fallback: first result if exact match not found
    item = data["results"][0]
    return {
        "genre": item.get("primaryGenreName", "") or "",
        "release_date": (item.get("releaseDate", "") or "")[:10],
    }


def _deezer_get(path: str, params: dict = None) -> dict | None:
    rate_limiter.acquire("deezer")
    try:
        resp = _deezer_session.get(f"{_DEEZER_BASE}{path}", params=params, timeout=10)
        if resp.status_code == 429:
            rate_limiter.record_429("deezer")
            return None
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.warning("Deezer API error on %s: %s", path, data["error"])
            return None
        rate_limiter.record_success("deezer")
        return data
    except requests.RequestException as e:
        logger.error("Deezer request failed (%s): %s", path, e)
        return None


def _deezer_search_artist(name: str) -> str | None:
    """Return best-match Deezer artist ID for a given name, or None.

    Tries the original name first; retries with a cleaned variant if needed.
    """
    norm_name = norm(name)
    for term in _search_variants(name):
        data = _deezer_get("/search/artist", {"q": term, "limit": 5})
        if not data or not data.get("data"):
            continue
        for artist in data["data"]:
            if norm(artist.get("name", "")) == norm_name:
                return str(artist["id"])
        return str(data["data"][0]["id"])
    return None


def _deezer_get_releases(deezer_id: str, cutoff: datetime,
                         allowed_kinds: set, ignore_kw: list[str]) -> list[Release]:
    releases = []
    index = 0
    while True:
        data = _deezer_get(f"/artist/{deezer_id}/albums",
                           {"limit": 50, "index": index})
        if not data or not data.get("data"):
            break
        for album in data["data"]:
            date_str = album.get("release_date", "")
            if not date_str:
                continue
            try:
                rel_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if rel_date < cutoff:
                continue
            is_upcoming = rel_date > datetime.utcnow()
            title = album.get("title", "")
            if any(kw in title.lower() for kw in ignore_kw):
                continue
            kind = album.get("record_type", "album").lower()
            if kind not in allowed_kinds:
                continue
            cover = album.get("cover_xl") or album.get("cover_medium") or ""
            releases.append(Release(
                artist=album.get("artist", {}).get("name", ""),
                title=title,
                release_date=date_str,
                kind=kind,
                source="deezer",
                source_url=album.get("link", ""),
                deezer_album_id=str(album.get("id", "")),
                artwork_url=cover,
                is_upcoming=is_upcoming,
            ))
        if data.get("next"):
            index += 50
        else:
            break
    return releases


def search_artist_candidates_deezer(name: str, limit: int = 5) -> list[dict]:
    """
    Return Deezer artist search candidates as [{name: str, id: str}].
    Tries name variants (& → and, punctuation stripped) if the original returns nothing.
    Used by _validate_artist() in library_service for album-overlap scoring.
    """
    results = []
    for term in _search_variants(name):
        data = _deezer_get("/search/artist", {"q": term, "limit": limit})
        if not data or not data.get("data"):
            continue
        for artist in data["data"]:
            aid = str(artist.get("id", ""))
            aname = artist.get("name", "")
            if aid:
                results.append({"name": aname, "id": aid})
        if results:
            break
    return results


def get_artist_albums_deezer(artist_id: str) -> list[dict]:
    """
    Return the album catalog for a Deezer artist ID as [{id, title, record_type}].
    Returns up to 100 albums (covers essentially all discographies).
    Used by _validate_artist() to score album-catalog overlap.
    """
    data = _deezer_get(f"/artist/{artist_id}/albums", {"limit": 100})
    if not data or not data.get("data"):
        return []
    return [
        {
            "id": str(album.get("id", "")),
            "title": album.get("title", ""),
            "record_type": album.get("record_type", "album"),
            "track_count": album.get("nb_tracks") or 0,
            "artwork_url": album.get("cover_xl") or album.get("cover_medium") or "",
            "release_date": album.get("release_date", ""),
            "explicit": bool(album.get("explicit_lyrics")),
        }
        for album in data["data"]
        if album.get("title")
    ]


def get_deezer_album_info(deezer_album_id: str) -> dict | None:
    """
    Fetch record_type and cover thumbnail URL for a Deezer album by ID.
    Returns {record_type: str, thumb_url: str} or None on API error.
    record_type values from Deezer: "album", "single", "compile" (EP/compilation).
    thumb_url is the medium cover (500x500) CDN URL — persists when Plex is offline.
    Used by enrich_deezer_release() (Stage 3 S3-2).
    """
    data = _deezer_get(f"/album/{deezer_album_id}")
    if not data:
        return None
    genres = data.get("genres", {}).get("data", [])
    genre = genres[0].get("name", "") if genres else ""
    return {
        "record_type": data.get("record_type", "") or "",
        "thumb_url": data.get("cover_medium", "") or data.get("cover", "") or "",
        "upc": data.get("upc", "") or "",
        "genre": genre or "",
    }


def get_deezer_artist_info(deezer_artist_id: str) -> dict | None:
    """Fetch artist-level stats from Deezer.

    Returns {nb_fan: int, nb_album: int} or None on error.
    Used by enrich_deezer_artist() (Stage 3).
    """
    data = _deezer_get(f"/artist/{deezer_artist_id}")
    if not data:
        return None
    return {
        "nb_fan": data.get("nb_fan", 0),
        "nb_album": data.get("nb_album", 0),
    }


def get_deezer_related_artists(deezer_artist_id: str, limit: int = 10) -> list[dict]:
    """Fetch related/similar artists from Deezer.

    Returns [{name: str, deezer_id: int, nb_fan: int}, ...].
    Free endpoint, no auth required.
    """
    data = _deezer_get(f"/artist/{deezer_artist_id}/related", {"limit": limit})
    if not data or not data.get("data"):
        return []
    results = []
    for a in data["data"]:
        results.append({
            "name": a.get("name", ""),
            "deezer_id": a.get("id"),
            "nb_fan": a.get("nb_fan", 0),
        })
    return results


def get_album_tracks_itunes(itunes_album_id: str) -> list[dict]:
    """Fetch track listing for an iTunes album by collectionId.

    Returns [{title, track_number, disc_number, duration_ms, preview_url}].
    Uses /lookup?id={id}&entity=song — first result is the collection wrapper.
    """
    data = _itunes_get("/lookup", {"id": itunes_album_id, "entity": "song"})
    if not data or not data.get("results"):
        return []
    tracks = []
    for item in data["results"]:
        if item.get("wrapperType") != "track":
            continue
        tracks.append({
            "title": item.get("trackName", ""),
            "track_number": item.get("trackNumber", 0),
            "disc_number": item.get("discNumber", 1),
            "duration_ms": item.get("trackTimeMillis", 0),
            "preview_url": item.get("previewUrl", ""),
        })
    return tracks


def get_album_tracks_deezer(deezer_album_id: str) -> list[dict]:
    """Fetch track listing for a Deezer album by album ID.

    Returns [{title, track_number, disc_number, duration_ms, preview_url}].
    """
    data = _deezer_get(f"/album/{deezer_album_id}/tracks", {"limit": 200})
    if not data or not data.get("data"):
        return []
    tracks = []
    for item in data["data"]:
        tracks.append({
            "title": item.get("title", ""),
            "track_number": item.get("track_position", 0),
            "disc_number": item.get("disk_number", 1),
            "duration_ms": (item.get("duration", 0)) * 1000,
            "preview_url": item.get("preview", ""),
        })
    return tracks


# ---------------------------------------------------------------------------
# MusicBrainz (no auth, 1 req/sec rate limit)
# ---------------------------------------------------------------------------

_mb_last_call: float = 0.0


def _mb_get(path: str, params: dict = None) -> dict | None:
    global _mb_last_call
    elapsed = time.monotonic() - _mb_last_call
    if elapsed < _MB_RATE_INTERVAL:
        time.sleep(_MB_RATE_INTERVAL - elapsed)
    _mb_last_call = time.monotonic()
    try:
        resp = requests.get(
            f"{_MB_BASE}{path}",
            params=params,
            headers={"User-Agent": _MB_USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("MusicBrainz request failed (%s): %s", path, e)
        return None


def _mb_resolve_via_spotify_id(spotify_artist_id: str) -> str | None:
    """
    Resolve a MusicBrainz MBID from a Spotify artist ID using MB's URL relationship lookup.
    More precise than name matching.
    """
    data = _mb_get("/url", {
        "resource": f"https://open.spotify.com/artist/{spotify_artist_id}",
        "inc": "artist-rels",
        "fmt": "json",
    })
    if not data:
        return None
    for rel in data.get("relations", []):
        if rel.get("target-type") == "artist":
            return rel.get("artist", {}).get("id")
    return None


def _mb_search_artist(name: str) -> str | None:
    """Return best-match MusicBrainz artist MBID by name, or None."""
    data = _mb_get("/artist", {"query": f'artist:"{name}"', "limit": 5, "fmt": "json"})
    if not data or not data.get("artists"):
        return None
    norm_name = norm(name)
    for artist in data["artists"]:
        if norm(artist.get("name", "")) == norm_name:
            return artist["id"]
    return data["artists"][0]["id"]


def _mb_get_releases(mbid: str, cutoff: datetime,
                     allowed_kinds: set, ignore_kw: list[str]) -> list[Release]:
    releases = []
    offset = 0
    while True:
        data = _mb_get("/release", {
            "artist": mbid,
            "limit": 100,
            "offset": offset,
            "fmt": "json",
            "inc": "release-groups",
        })
        if not data or not data.get("releases"):
            break
        for rel in data["releases"]:
            date_str = rel.get("date", "")
            if not date_str or len(date_str) < 10:
                continue
            try:
                rel_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if rel_date < cutoff:
                continue
            is_upcoming = rel_date > datetime.utcnow()
            title = rel.get("title", "")
            if any(kw in title.lower() for kw in ignore_kw):
                continue
            kind = rel.get("release-group", {}).get("primary-type", "album").lower()
            if kind not in allowed_kinds:
                continue
            releases.append(Release(
                artist="",  # filled by caller
                title=title,
                release_date=date_str[:10],
                kind=kind,
                source="musicbrainz",
                source_url=f"https://musicbrainz.org/release/{rel['id']}",
                is_upcoming=is_upcoming,
            ))
        release_count = data.get("release-count", 0)
        offset += 100
        if offset >= release_count:
            break
    return releases


# ---------------------------------------------------------------------------
# Spotify (requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)
# ---------------------------------------------------------------------------

def _spotify_available() -> bool:
    return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)


_spotify_rate_interval: float = 60.0 / max(config.SPOTIFY_RATE_LIMIT_RPM, 1)
_spotify_last_call: float = 0.0


def _spotify_rate_limit() -> None:
    """Sleep if needed to stay under SPOTIFY_RATE_LIMIT_RPM. Call before every Spotify request."""
    global _spotify_last_call
    elapsed = time.time() - _spotify_last_call
    if elapsed < _spotify_rate_interval:
        time.sleep(_spotify_rate_interval - elapsed)
    _spotify_last_call = time.time()


def _spotify_get_releases(name: str, cutoff: datetime,
                          allowed_kinds: set, ignore_kw: list[str],
                          spotify_artist_id: str = None) -> list[Release]:
    if not _spotify_available():
        return []
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        ))
    except Exception as e:
        logger.error("Spotify client init failed: %s", e)
        return []

    cutoff_str = cutoff.strftime("%Y-%m-%d")
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    releases = []

    artist_id = spotify_artist_id
    if not artist_id:
        try:
            _spotify_rate_limit()
            results = sp.search(q=f'artist:"{name}"', type="artist", limit=5)
            artists = results.get("artists", {}).get("items", [])
            if not artists:
                return []
            norm_name = norm(name)
            artist = next((a for a in artists if norm(a["name"]) == norm_name), artists[0])
            artist_id = artist["id"]
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                logger.warning("Spotify rate limit hit searching '%s' — falling back to iTunes", name)
                return []
            logger.error("Spotify artist search failed for '%s': %s", name, e)
            return []

    try:
        groups = ",".join(k for k in ("album", "single") if k in allowed_kinds)
        offset = 0
        while True:
            _spotify_rate_limit()
            resp = sp.artist_albums(artist_id, include_groups=groups, limit=50, offset=offset)
            if not resp or not resp.get("items"):
                break
            for album in resp["items"]:
                date_str = album.get("release_date", "")
                if not date_str or date_str < cutoff_str:
                    continue
                title = album.get("name", "")
                if any(kw in title.lower() for kw in ignore_kw):
                    continue
                kind = album.get("album_type", "album").lower()
                if kind not in allowed_kinds:
                    continue
                releases.append(Release(
                    artist=name,
                    title=title,
                    release_date=date_str,
                    kind=kind,
                    source="spotify",
                    source_url=album.get("external_urls", {}).get("spotify", ""),
                    spotify_album_id=album.get("id", ""),
                    is_upcoming=date_str > today_str,
                ))
            if resp.get("next"):
                offset += 50
            else:
                break
    except Exception as e:
        msg = str(e)
        if "429" in msg or "rate" in msg.lower():
            logger.warning("Spotify rate limit hit fetching albums for '%s' — falling back to iTunes", name)
            return []
        logger.error("Spotify artist albums failed for '%s': %s", name, e)

    return releases


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_new_releases_for_artist(
    artist_name: str,
    days_ago: int,
    ignore_keywords: list[str] | None = None,
    cached_ids: dict | None = None,
    spotify_artist_id: str = None,
    force_refresh: bool = False,
    allowed_kinds: set | None = None,
) -> tuple[list[Release], dict]:
    """
    Discover new releases for a single artist using the configured provider chain:
      Spotify → iTunes → Deezer  (MusicBrainz only when MUSIC_API_PROVIDER=musicbrainz)

    Results are cached in rythmx.db for 7 days (release_cache table). Set force_refresh=True
    to bypass the cache and re-fetch from the provider.

    Returns (releases, resolved_ids) where resolved_ids contains any provider IDs
    discovered during this call — caller should write them to artist_identity_cache.
    Upcoming (future-dated) releases are stored in the cache but NOT returned to the
    caller — they're filtered here and available for a future "Upcoming" UI feature.

    artist_name       — Last.fm artist name
    days_ago          — how far back to look (lookback_days)
    ignore_keywords   — release title substrings to skip (e.g. remix, remaster)
    cached_ids        — dict from rythmx_store.get_cached_artist() with cached provider IDs
    spotify_artist_id — Spotify artist ID from SoulSync DB (skips name-based search)
    force_refresh     — bypass the 7-day cache and re-fetch from provider
    """
    from app.db import rythmx_store

    cutoff = datetime.utcnow() - timedelta(days=days_ago)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    ignore_kw = [k.strip().lower() for k in (ignore_keywords or [])]
    if allowed_kinds is None:
        allowed_kinds = {k.strip().lower() for k in config.RELEASE_KINDS.split(",") if k.strip()}
    provider = config.MUSIC_API_PROVIDER

    # --- Release cache check (7-day TTL) ---
    if not force_refresh:
        cached = rythmx_store.get_cached_releases(artist_name, max_age_days=7)
        if cached is not None:
            releases = [
                r for r in cached
                if not r.is_upcoming
                and (r.release_date or "") >= cutoff_str
                and r.kind in allowed_kinds
            ]
            logger.debug("Release cache hit for '%s': %d/%d in window",
                         artist_name, len(releases), len(cached))
            return releases, {}

    ids = dict(cached_ids or {})
    sp_id = spotify_artist_id or ids.get("spotify_artist_id")
    if sp_id:
        ids["spotify_artist_id"] = sp_id

    def _done(releases_list: list, resolved_ids: dict):
        """Save all fetched releases (including upcoming) to cache; return non-upcoming.
        Always saves — even an empty list writes a sentinel so quiet artists are not
        re-fetched from the API on every run within the cache TTL window."""
        rythmx_store.save_releases_to_cache(artist_name, releases_list)
        return [r for r in releases_list if not r.is_upcoming], resolved_ids

    # --- Spotify ---
    if provider in ("spotify", "auto") and _spotify_available():
        releases = _spotify_get_releases(artist_name, cutoff, allowed_kinds, ignore_kw,
                                         spotify_artist_id=sp_id)
        if releases:
            logger.info("Spotify: %d releases for '%s'", len(releases), artist_name)
            return _done(releases, ids)
        if provider == "spotify":
            return _done([], ids)

    # --- iTunes (primary free provider) ---
    if provider in ("itunes", "auto"):
        it_id = ids.get("itunes_artist_id")
        if not it_id:
            it_id = _itunes_search_artist(artist_name)
        if it_id:
            ids["itunes_artist_id"] = it_id
            releases = _itunes_get_releases(it_id, cutoff, allowed_kinds, ignore_kw)
            if releases:
                logger.info("iTunes: %d releases for '%s'", len(releases), artist_name)
                return _done(releases, ids)
        if provider == "itunes":
            return _done([], ids)

    # --- Deezer (fallback) ---
    if provider in ("deezer", "auto"):
        deezer_id = ids.get("deezer_artist_id")
        if not deezer_id:
            deezer_id = _deezer_search_artist(artist_name)
        if deezer_id:
            ids["deezer_artist_id"] = deezer_id
            releases = _deezer_get_releases(deezer_id, cutoff, allowed_kinds, ignore_kw)
            if releases:
                logger.info("Deezer: %d releases for '%s'", len(releases), artist_name)
                return _done(releases, ids)
        if provider == "deezer":
            return _done([], ids)

    # --- MusicBrainz (explicit only — not in auto chain) ---
    # MB is excluded from "auto" because it hits a 1 req/sec rate limit and
    # connection-resets from Docker networking. Set MUSIC_API_PROVIDER=musicbrainz
    # to use it explicitly.
    if provider == "musicbrainz":
        mb_id = ids.get("mb_artist_id")
        if not mb_id and sp_id:
            mb_id = _mb_resolve_via_spotify_id(sp_id)
        if not mb_id:
            mb_id = _mb_search_artist(artist_name)
        if mb_id:
            ids["mb_artist_id"] = mb_id
            releases = _mb_get_releases(mb_id, cutoff, allowed_kinds, ignore_kw)
            logger.debug("MusicBrainz: %d releases for '%s'", len(releases), artist_name)
            return _done(releases, ids)

    logger.info("No releases found for '%s' via any provider", artist_name)
    return _done([], ids)


def get_active_provider() -> str:
    """Return the effective provider label for display in Settings/API."""
    p = config.MUSIC_API_PROVIDER
    if p != "auto":
        return p
    return "spotify (auto)" if _spotify_available() else "itunes (auto)"
