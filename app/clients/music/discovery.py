"""
Public release discovery orchestration across providers.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from app import config
from .shared import Release
from .spotify import _spotify_available, _spotify_get_releases
from .itunes import _itunes_search_artist, _itunes_get_releases
from .deezer import _deezer_search_artist, _deezer_get_releases
from .musicbrainz import _mb_resolve_via_spotify_id, _mb_search_artist, _mb_get_releases

logger = logging.getLogger(__name__)


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
    Discover new releases for a single artist using the configured provider chain.
    """
    _ = force_refresh  # retained for backward compatibility
    cutoff = datetime.utcnow() - timedelta(days=days_ago)
    ignore_kw = [k.strip().lower() for k in (ignore_keywords or [])]
    if allowed_kinds is None:
        allowed_kinds = {k.strip().lower() for k in config.RELEASE_KINDS.split(",") if k.strip()}
    provider = config.MUSIC_API_PROVIDER

    ids = dict(cached_ids or {})
    sp_id = spotify_artist_id or ids.get("spotify_artist_id")
    if sp_id:
        ids["spotify_artist_id"] = sp_id

    def _finalize(releases_list: list[Release], resolved_ids: dict) -> tuple[list[Release], dict]:
        return [r for r in releases_list if not r.is_upcoming], resolved_ids

    if provider in ("spotify", "auto") and _spotify_available():
        releases = _spotify_get_releases(
            artist_name,
            cutoff,
            allowed_kinds,
            ignore_kw,
            spotify_artist_id=sp_id,
        )
        if releases:
            logger.info("Spotify: %d releases for '%s'", len(releases), artist_name)
            return _finalize(releases, ids)
        if provider == "spotify":
            return _finalize([], ids)

    if provider in ("itunes", "auto"):
        it_id = ids.get("itunes_artist_id")
        if not it_id:
            it_id = _itunes_search_artist(artist_name)
        if it_id:
            ids["itunes_artist_id"] = it_id
            releases = _itunes_get_releases(it_id, cutoff, allowed_kinds, ignore_kw)
            if releases:
                logger.info("iTunes: %d releases for '%s'", len(releases), artist_name)
                return _finalize(releases, ids)
        if provider == "itunes":
            return _finalize([], ids)

    if provider in ("deezer", "auto"):
        deezer_id = ids.get("deezer_artist_id")
        if not deezer_id:
            deezer_id = _deezer_search_artist(artist_name)
        if deezer_id:
            ids["deezer_artist_id"] = deezer_id
            releases = _deezer_get_releases(deezer_id, cutoff, allowed_kinds, ignore_kw)
            if releases:
                logger.info("Deezer: %d releases for '%s'", len(releases), artist_name)
                return _finalize(releases, ids)
        if provider == "deezer":
            return _finalize([], ids)

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
            return _finalize(releases, ids)

    logger.info("No releases found for '%s' via any provider", artist_name)
    return _finalize([], ids)


def get_active_provider() -> str:
    """Return the effective provider label for display."""
    p = config.MUSIC_API_PROVIDER
    if p != "auto":
        return p
    return "spotify (auto)" if _spotify_available() else "itunes (auto)"

