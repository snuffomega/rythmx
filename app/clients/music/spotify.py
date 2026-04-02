"""
Spotify provider helpers.
"""
from __future__ import annotations

from datetime import datetime
import logging
import time

from app import config
from .shared import Release, norm

logger = logging.getLogger(__name__)

_spotify_rate_interval: float = 60.0 / max(config.SPOTIFY_RATE_LIMIT_RPM, 1)
_spotify_last_call: float = 0.0


def _spotify_available() -> bool:
    return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)


def _spotify_rate_limit() -> None:
    """Sleep if needed to stay under SPOTIFY_RATE_LIMIT_RPM."""
    global _spotify_last_call
    elapsed = time.time() - _spotify_last_call
    if elapsed < _spotify_rate_interval:
        time.sleep(_spotify_rate_interval - elapsed)
    _spotify_last_call = time.time()


def _spotify_get_releases(
    name: str,
    cutoff: datetime,
    allowed_kinds: set,
    ignore_kw: list[str],
    spotify_artist_id: str = None,
) -> list[Release]:
    if not _spotify_available():
        return []
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials

        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=config.SPOTIFY_CLIENT_ID,
                client_secret=config.SPOTIFY_CLIENT_SECRET,
            )
        )
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
                logger.warning("Spotify rate limit hit searching '%s' - falling back to iTunes", name)
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
                releases.append(
                    Release(
                        artist=name,
                        title=title,
                        release_date=date_str,
                        kind=kind,
                        source="spotify",
                        source_url=album.get("external_urls", {}).get("spotify", ""),
                        spotify_album_id=album.get("id", ""),
                        is_upcoming=date_str > today_str,
                    )
                )
            if resp.get("next"):
                offset += 50
            else:
                break
    except Exception as e:
        msg = str(e)
        if "429" in msg or "rate" in msg.lower():
            logger.warning("Spotify rate limit hit fetching albums for '%s' - falling back to iTunes", name)
            return []
        logger.error("Spotify artist albums failed for '%s': %s", name, e)

    return releases

