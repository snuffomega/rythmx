"""
Compatibility facade for the modularized music client.

This module preserves the historical import surface while delegating
implementation to provider-focused modules under app.clients.music.
"""
from __future__ import annotations

from app.clients.music.shared import norm, Release
from app.clients.music.itunes import (
    _itunes_get,
    _search_variants,
    _itunes_search_artist,
    _itunes_get_releases,
    search_artist_candidates_itunes,
    _derive_collection_type,
    get_artist_albums_itunes,
    get_artist_top_tracks_itunes,
    get_album_itunes_rich,
    get_album_tracks_itunes,
)
from app.clients.music.deezer import (
    _deezer_get,
    _deezer_search_artist,
    _deezer_get_releases,
    search_artist_candidates_deezer,
    get_artist_albums_deezer,
    get_deezer_album_info,
    get_deezer_artist_info,
    get_deezer_related_artists,
    get_album_tracks_deezer,
)
from app.clients.music.musicbrainz import (
    _mb_get,
    _mb_resolve_via_spotify_id,
    _mb_search_artist,
    _mb_get_releases,
)
from app.clients.music.spotify import (
    _spotify_available,
    _spotify_rate_limit,
    _spotify_get_releases,
)
from app.clients.music.discovery import (
    get_new_releases_for_artist,
    get_active_provider,
)

__all__ = [
    "norm",
    "Release",
    "_itunes_get",
    "_search_variants",
    "_itunes_search_artist",
    "_itunes_get_releases",
    "search_artist_candidates_itunes",
    "_derive_collection_type",
    "get_artist_albums_itunes",
    "get_artist_top_tracks_itunes",
    "get_album_itunes_rich",
    "_deezer_get",
    "_deezer_search_artist",
    "_deezer_get_releases",
    "search_artist_candidates_deezer",
    "get_artist_albums_deezer",
    "get_deezer_album_info",
    "get_deezer_artist_info",
    "get_deezer_related_artists",
    "get_album_tracks_itunes",
    "get_album_tracks_deezer",
    "_mb_get",
    "_mb_resolve_via_spotify_id",
    "_mb_search_artist",
    "_mb_get_releases",
    "_spotify_available",
    "_spotify_rate_limit",
    "_spotify_get_releases",
    "get_new_releases_for_artist",
    "get_active_provider",
]

