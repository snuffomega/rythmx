"""
Deezer provider and related lookup helpers.
"""
from __future__ import annotations

from datetime import datetime
import logging

import requests

from app.services.api_orchestrator import rate_limiter
from .shared import Release, norm
from .itunes import _search_variants

logger = logging.getLogger(__name__)

DEEZER_BASE = "https://api.deezer.com"

_deezer_session = requests.Session()
_deezer_session.headers["Accept"] = "application/json"


def _deezer_get(path: str, params: dict = None) -> dict | None:
    rate_limiter.acquire("deezer")
    try:
        resp = _deezer_session.get(f"{DEEZER_BASE}{path}", params=params, timeout=10)
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
    """Return best-match Deezer artist ID for a given name, or None."""
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


def _deezer_get_releases(
    deezer_id: str,
    cutoff: datetime,
    allowed_kinds: set,
    ignore_kw: list[str],
) -> list[Release]:
    releases = []
    index = 0
    while True:
        data = _deezer_get(f"/artist/{deezer_id}/albums", {"limit": 50, "index": index})
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
            releases.append(
                Release(
                    artist=album.get("artist", {}).get("name", ""),
                    title=title,
                    release_date=date_str,
                    kind=kind,
                    source="deezer",
                    source_url=album.get("link", ""),
                    deezer_album_id=str(album.get("id", "")),
                    artwork_url=cover,
                    is_upcoming=is_upcoming,
                )
            )
        if data.get("next"):
            index += 50
        else:
            break
    return releases


def search_artist_candidates_deezer(name: str, limit: int = 5) -> list[dict]:
    """Return Deezer artist search candidates as [{name, id}]."""
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
    """Return the album catalog for a Deezer artist ID."""
    data = _deezer_get(f"/artist/{artist_id}/albums", {"limit": 100})
    if not data or not data.get("data"):
        return []
    return [
        {
            "id": str(album.get("id", "")),
            "title": album.get("title", ""),
            "record_type": album.get("record_type", "album"),
            "track_count": album.get("nb_tracks") or None,
            "artwork_url": album.get("cover_xl") or album.get("cover_medium") or "",
            "release_date": album.get("release_date", ""),
            "explicit": bool(album.get("explicit_lyrics")),
        }
        for album in data["data"]
        if album.get("title")
    ]


def get_deezer_album_info(deezer_album_id: str) -> dict | None:
    """Fetch record_type and metadata for a Deezer album by ID."""
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
    """Fetch artist-level stats from Deezer."""
    data = _deezer_get(f"/artist/{deezer_artist_id}")
    if not data:
        return None
    return {
        "nb_fan": data.get("nb_fan", 0),
        "nb_album": data.get("nb_album", 0),
    }


def get_deezer_related_artists(deezer_artist_id: str, limit: int = 10) -> list[dict]:
    """Fetch related/similar artists from Deezer."""
    data = _deezer_get(f"/artist/{deezer_artist_id}/related", {"limit": limit})
    if not data or not data.get("data"):
        return []
    results = []
    for a in data["data"]:
        results.append(
            {
                "name": a.get("name", ""),
                "deezer_id": a.get("id"),
                "nb_fan": a.get("nb_fan", 0),
            }
        )
    return results


def get_album_tracks_deezer(deezer_album_id: str) -> list[dict]:
    """Fetch track listing for a Deezer album by album ID."""
    data = _deezer_get(f"/album/{deezer_album_id}/tracks", {"limit": 200})
    if not data or not data.get("data"):
        return []
    tracks = []
    for item in data["data"]:
        tracks.append(
            {
                "title": item.get("title", ""),
                "track_number": item.get("track_position", 0),
                "disc_number": item.get("disk_number", 1),
                "duration_ms": (item.get("duration", 0)) * 1000,
                "preview_url": item.get("preview", ""),
            }
        )
    return tracks
