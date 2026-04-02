"""
iTunes provider and related lookup helpers.
"""
from __future__ import annotations

from datetime import datetime
import logging
import re

import requests

from app.services.api_orchestrator import rate_limiter
from .shared import MB_USER_AGENT, Release, norm

logger = logging.getLogger(__name__)

ITUNES_BASE = "https://itunes.apple.com"

_itunes_session = requests.Session()
_itunes_session.headers["Accept"] = "application/json"
_itunes_session.headers["User-Agent"] = MB_USER_AGENT


def _itunes_get(path: str, params: dict = None) -> dict | None:
    rate_limiter.acquire("itunes")
    try:
        resp = _itunes_session.get(f"{ITUNES_BASE}{path}", params=params, timeout=15)
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
    Return search term variants to try for a given artist name.
    """
    variants = [name]
    cleaned = name.replace("&", "and").replace("+", "and")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned != name:
        variants.append(cleaned)
    return variants


def _itunes_search_artist(name: str) -> str | None:
    """Return best-match iTunes artistId for a given name, or None."""
    norm_name = norm(name)
    for term in _search_variants(name):
        data = _itunes_get(
            "/search",
            {
                "term": term,
                "entity": "musicArtist",
                "media": "music",
                "limit": 5,
            },
        )
        if not data or not data.get("results"):
            continue
        for artist in data["results"]:
            if norm(artist.get("artistName", "")) == norm_name:
                return str(artist["artistId"])
        return str(data["results"][0]["artistId"])
    return None


def _itunes_get_releases(
    itunes_artist_id: str,
    cutoff: datetime,
    allowed_kinds: set,
    ignore_kw: list[str],
) -> list[Release]:
    """Return new releases for an iTunes artist ID since cutoff."""
    data = _itunes_get(
        "/lookup",
        {
            "id": itunes_artist_id,
            "entity": "album",
            "limit": 200,
        },
    )
    if not data or not data.get("results"):
        return []

    releases = []
    for item in data["results"]:
        if item.get("wrapperType") != "collection":
            continue
        date_str = item.get("releaseDate", "")
        if not date_str:
            continue
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
        kind = item.get("collectionType", "album").lower()
        if kind == "album":
            lower_title = title.lower()
            if "- single" in lower_title or lower_title.endswith(" single"):
                kind = "single"
            elif " - ep" in lower_title or lower_title.endswith(" ep"):
                kind = "ep"
        if kind not in allowed_kinds:
            if "album" in allowed_kinds:
                kind = "album"
            else:
                continue
        raw_art = item.get("artworkUrl100", "")
        artwork = raw_art.replace("100x100bb", "600x600bb") if raw_art else ""
        releases.append(
            Release(
                artist=item.get("artistName", ""),
                title=title,
                release_date=date_str[:10],
                kind=kind,
                source="itunes",
                source_url=item.get("collectionViewUrl", ""),
                itunes_album_id=str(item.get("collectionId", "")),
                artwork_url=artwork,
                is_upcoming=is_upcoming,
            )
        )
    return releases


def search_artist_candidates_itunes(name: str, limit: int = 5) -> list[dict]:
    """Return iTunes artist search candidates as [{name, id}]."""
    results = []
    for term in _search_variants(name):
        data = _itunes_get(
            "/search",
            {
                "term": term,
                "entity": "musicArtist",
                "media": "music",
                "limit": limit,
            },
        )
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
    """
    data = _itunes_get(
        "/lookup",
        {
            "id": itunes_artist_id,
            "entity": "album",
            "limit": 200,
        },
    )
    if not data or not data.get("results"):
        return []
    results = []
    for item in data["results"]:
        if item.get("wrapperType") != "collection" or not item.get("collectionName"):
            continue
        raw_art = item.get("artworkUrl100", "")
        results.append(
            {
                "id": str(item.get("collectionId", "")),
                "title": item.get("collectionName", ""),
                "track_count": item.get("trackCount") or 0,
                "record_type": _derive_collection_type(item),
                "artwork_url": raw_art.replace("100x100bb", "600x600bb") if raw_art else "",
                "release_date": item.get("releaseDate", ""),
                "explicit": item.get("collectionExplicitness", "") == "explicit",
                "label": item.get("copyright", ""),
                "genre": item.get("primaryGenreName", ""),
            }
        )
    return results


def get_artist_top_tracks_itunes(itunes_artist_id: str, limit: int = 10) -> list[str]:
    """
    Return top track title strings for an iTunes artist ID.
    """
    data = _itunes_get(
        "/lookup",
        {
            "id": itunes_artist_id,
            "entity": "song",
            "attribute": "artistTerm",
            "limit": limit + 1,
        },
    )
    if not data or not data.get("results"):
        return []
    titles = [
        r.get("trackName", "")
        for r in data["results"]
        if r.get("wrapperType") == "track" and r.get("trackName")
    ]
    logger.debug("iTunes top tracks for ID %s: %d", itunes_artist_id, len(titles))
    return titles[:limit]


def get_album_itunes_rich(itunes_album_id: str) -> dict | None:
    """
    Fetch genre and release_date for an album from iTunes by collectionId.
    """
    data = _itunes_get("/lookup", {"id": itunes_album_id, "entity": "album"})
    if not data or not data.get("results"):
        return None
    for item in data["results"]:
        if item.get("wrapperType") == "collection" and str(item.get("collectionId", "")) == str(itunes_album_id):
            return {
                "genre": item.get("primaryGenreName", "") or "",
                "release_date": (item.get("releaseDate", "") or "")[:10],
            }
    item = data["results"][0]
    return {
        "genre": item.get("primaryGenreName", "") or "",
        "release_date": (item.get("releaseDate", "") or "")[:10],
    }


def get_album_tracks_itunes(itunes_album_id: str) -> list[dict]:
    """Fetch track listing for an iTunes album by collectionId."""
    data = _itunes_get("/lookup", {"id": itunes_album_id, "entity": "song"})
    if not data or not data.get("results"):
        return []
    tracks = []
    for item in data["results"]:
        if item.get("wrapperType") != "track":
            continue
        tracks.append(
            {
                "title": item.get("trackName", ""),
                "track_number": item.get("trackNumber", 0),
                "disc_number": item.get("discNumber", 1),
                "duration_ms": item.get("trackTimeMillis", 0),
                "preview_url": item.get("previewUrl", ""),
            }
        )
    return tracks

