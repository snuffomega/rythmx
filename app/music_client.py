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
No DB access — pure API calls. Caller is responsible for caching via cc_store.
"""
import unicodedata
import re
import time
import logging
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
from app import config

logger = logging.getLogger(__name__)

_ARTICLES = frozenset({"the", "a", "an"})
_ITUNES_BASE = "https://itunes.apple.com"
_ITUNES_RATE_INTERVAL = 3.1   # seconds between iTunes requests (20/min = 1 per 3s, add margin)
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


# ---------------------------------------------------------------------------
# iTunes Search API (no auth, 20 req/min)
# ---------------------------------------------------------------------------

_itunes_last_call: float = 0.0
_itunes_session = requests.Session()
_itunes_session.headers["Accept"] = "application/json"
_itunes_session.headers["User-Agent"] = _MB_USER_AGENT


def _itunes_get(path: str, params: dict = None) -> dict | None:
    global _itunes_last_call
    elapsed = time.monotonic() - _itunes_last_call
    if elapsed < _ITUNES_RATE_INTERVAL:
        time.sleep(_ITUNES_RATE_INTERVAL - elapsed)
    _itunes_last_call = time.monotonic()
    try:
        resp = _itunes_session.get(f"{_ITUNES_BASE}{path}", params=params, timeout=15)
        resp.raise_for_status()
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
        if rel_date > datetime.utcnow():
            continue  # skip future-dated pre-announcements
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
        releases.append(Release(
            artist=item.get("artistName", ""),
            title=title,
            release_date=date_str[:10],
            kind=kind,
            source="itunes",
            source_url=item.get("collectionViewUrl", ""),
            itunes_album_id=str(item.get("collectionId", "")),
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


def _deezer_get(path: str, params: dict = None) -> dict | None:
    try:
        resp = _deezer_session.get(f"{_DEEZER_BASE}{path}", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.warning("Deezer API error on %s: %s", path, data["error"])
            return None
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
            if rel_date > datetime.utcnow():
                continue  # skip future-dated pre-announcements
            title = album.get("title", "")
            if any(kw in title.lower() for kw in ignore_kw):
                continue
            kind = album.get("record_type", "album").lower()
            if kind not in allowed_kinds:
                continue
            releases.append(Release(
                artist=album.get("artist", {}).get("name", ""),
                title=title,
                release_date=date_str,
                kind=kind,
                source="deezer",
                source_url=album.get("link", ""),
                deezer_album_id=str(album.get("id", "")),
            ))
        if data.get("next"):
            index += 50
        else:
            break
    return releases


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
    releases = []

    artist_id = spotify_artist_id
    if not artist_id:
        try:
            results = sp.search(q=f'artist:"{name}"', type="artist", limit=5)
            artists = results.get("artists", {}).get("items", [])
            if not artists:
                return []
            norm_name = norm(name)
            artist = next((a for a in artists if norm(a["name"]) == norm_name), artists[0])
            artist_id = artist["id"]
        except Exception as e:
            logger.error("Spotify artist search failed for '%s': %s", name, e)
            return []

    try:
        groups = ",".join(k for k in ("album", "single") if k in allowed_kinds)
        offset = 0
        while True:
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
                ))
            if resp.get("next"):
                offset += 50
            else:
                break
    except Exception as e:
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
) -> tuple[list[Release], dict]:
    """
    Discover new releases for a single artist using the configured provider chain:
      Spotify → iTunes → Deezer  (MusicBrainz only when MUSIC_API_PROVIDER=musicbrainz)

    Returns (releases, resolved_ids) where resolved_ids contains any provider IDs
    discovered during this call — caller should write them to artist_identity_cache.

    artist_name       — Last.fm artist name
    days_ago          — how far back to look (cc_lookback_days)
    ignore_keywords   — release title substrings to skip (e.g. remix, remaster)
    cached_ids        — dict from cc_store.get_cached_artist() with cached provider IDs
    spotify_artist_id — Spotify artist ID from SoulSync DB (skips name-based search)
    """
    cutoff = datetime.utcnow() - timedelta(days=days_ago)
    ignore_kw = [k.strip().lower() for k in (ignore_keywords or [])]
    allowed_kinds = {k.strip().lower() for k in config.CC_RELEASE_KINDS.split(",") if k.strip()}
    provider = config.MUSIC_API_PROVIDER

    ids = dict(cached_ids or {})
    sp_id = spotify_artist_id or ids.get("spotify_artist_id")
    if sp_id:
        ids["spotify_artist_id"] = sp_id

    # --- Spotify ---
    if provider in ("spotify", "auto") and _spotify_available():
        releases = _spotify_get_releases(artist_name, cutoff, allowed_kinds, ignore_kw,
                                         spotify_artist_id=sp_id)
        if releases:
            logger.info("Spotify: %d releases for '%s'", len(releases), artist_name)
            return releases, ids
        if provider == "spotify":
            return [], ids

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
                return releases, ids
        if provider == "itunes":
            return [], ids

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
                return releases, ids
        if provider == "deezer":
            return [], ids

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
            return releases, ids

    logger.info("No releases found for '%s' via any provider", artist_name)
    return [], ids


def get_active_provider() -> str:
    """Return the effective provider label for display in Settings/API."""
    p = config.MUSIC_API_PROVIDER
    if p != "auto":
        return p
    return "spotify (auto)" if _spotify_available() else "itunes (auto)"
