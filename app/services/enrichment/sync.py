"""
sync.py — Stage 1 library sync + release pruning.

Delegates to the active library backend (plex_reader or soulsync_reader).
"""
import logging

from app.db.rythmx_store import _connect

logger = logging.getLogger(__name__)


def sync_library() -> dict:
    """
    Stage 1: Walk the active library backend → write lib_* tables.
    Routes to the correct backend (plex_reader or soulsync_reader) via get_library_reader().
    After sync, prunes lib_releases rows older than 180 days with is_owned=0.
    Returns {artist_count, album_count, track_count, sync_duration_s}.
    """
    from app.db import get_library_reader
    result = get_library_reader().sync_library()
    _prune_old_releases()
    return result


def _prune_old_releases() -> None:
    """Delete lib_releases rows older than 180 days that are not owned.
    Owned releases are kept indefinitely. Called after each library sync.
    """
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM lib_releases "
                "WHERE is_owned = 0 "
                "AND first_seen_at < datetime('now', '-180 days')"
            )
    except Exception as e:
        logger.warning("prune_old_releases failed (table may not exist yet): %s", e)
