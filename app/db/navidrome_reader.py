"""
navidrome_reader.py — Navidrome library backend (not yet implemented).

Stub implementation. All functions return safe empty values or raise
NotImplementedError for write operations. Select this backend via
LIBRARY_BACKEND=navidrome or the Settings UI.
"""
import logging

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED = (
    "Navidrome backend is not yet implemented. "
    "Set LIBRARY_BACKEND=soulsync or LIBRARY_BACKEND=plex."
)


def sync_library() -> dict:
    raise NotImplementedError(_NOT_IMPLEMENTED)


def is_db_accessible() -> bool:
    return False


def get_track_count() -> int:
    return 0


def get_soulsync_artist_id(artist_name: str):
    return None


def get_spotify_artist_id(artist_name: str):
    return None


def get_deezer_artist_id(artist_name: str):
    return None


def get_itunes_artist_id(artist_name: str):
    return None


def check_album_owned(*args, **kwargs):
    return None


def check_owned_exact(spotify_track_id: str):
    return None


def check_owned_deezer(deezer_track_id: str):
    return None


def find_track_by_name(artist_name: str, track_title: str):
    return None


def get_all_tracks_for_artist(artist_id: str) -> list:
    return []


def get_tracks_for_album(artist_id: str, album_title: str) -> list:
    return []


def get_discovery_pool(**kwargs) -> list:
    return []


def get_similar_artists_map(**kwargs) -> dict:
    return {}
