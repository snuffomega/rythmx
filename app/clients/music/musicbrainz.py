"""
MusicBrainz provider helpers.
"""
from __future__ import annotations

from datetime import datetime
import logging
import time

import requests

from .shared import MB_USER_AGENT, Release, norm

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
MB_RATE_INTERVAL = 1.1
_mb_last_call: float = 0.0


def _mb_get(path: str, params: dict = None) -> dict | None:
    global _mb_last_call
    elapsed = time.monotonic() - _mb_last_call
    if elapsed < MB_RATE_INTERVAL:
        time.sleep(MB_RATE_INTERVAL - elapsed)
    _mb_last_call = time.monotonic()
    try:
        resp = requests.get(
            f"{MB_BASE}{path}",
            params=params,
            headers={"User-Agent": MB_USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error("MusicBrainz request failed (%s): %s", path, e)
        return None


def _mb_resolve_via_spotify_id(spotify_artist_id: str) -> str | None:
    """Resolve a MusicBrainz MBID from a Spotify artist ID."""
    data = _mb_get(
        "/url",
        {
            "resource": f"https://open.spotify.com/artist/{spotify_artist_id}",
            "inc": "artist-rels",
            "fmt": "json",
        },
    )
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


def _mb_get_releases(
    mbid: str,
    cutoff: datetime,
    allowed_kinds: set,
    ignore_kw: list[str],
) -> list[Release]:
    releases = []
    offset = 0
    while True:
        data = _mb_get(
            "/release",
            {
                "artist": mbid,
                "limit": 100,
                "offset": offset,
                "fmt": "json",
                "inc": "release-groups",
            },
        )
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
            releases.append(
                Release(
                    artist="",
                    title=title,
                    release_date=date_str[:10],
                    kind=kind,
                    source="musicbrainz",
                    source_url=f"https://musicbrainz.org/release/{rel['id']}",
                    is_upcoming=is_upcoming,
                )
            )
        release_count = data.get("release-count", 0)
        offset += 100
        if offset >= release_count:
            break
    return releases

