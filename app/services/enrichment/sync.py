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
    pruned_orphans = _prune_orphan_artists()
    result["orphan_artists_pruned"] = pruned_orphans
    if pruned_orphans:
        logger.info("sync_library: pruned %d orphan artists (no active albums)", pruned_orphans)

    # Local-only album artwork hydrate right after sync (navidrome + MUSIC_DIR).
    # Full Stage 1.2 still runs afterward for remote fallback sources.
    try:
        from app.config import MUSIC_DIR
        if MUSIC_DIR:
            from app.services.enrichment.art_album import hydrate_local_album_art_after_sync
            local_art = hydrate_local_album_art_after_sync(batch_size=2000)
            result["local_album_art"] = local_art
            logger.info(
                "sync_library: local_album_art processed=%d enriched=%d skipped=%d remaining=%d",
                local_art.get("processed", 0),
                local_art.get("enriched", 0),
                local_art.get("skipped", 0),
                local_art.get("remaining", 0),
            )
    except Exception as exc:
        logger.warning("sync_library: local album art hydrate skipped: %s", exc)

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


def _prune_orphan_artists() -> int:
    """
    Tombstone artists with no active albums.

    Active means removed_at IS NULL. Artists are soft-deleted (removed_at set)
    so empty artist pages do not appear after sync/repair runs.
    """
    try:
        with _connect() as conn:
            conn.execute(
                """
                UPDATE lib_artists
                   SET removed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE removed_at IS NULL
                   AND NOT EXISTS (
                        SELECT 1
                        FROM lib_albums
                        WHERE lib_albums.artist_id = lib_artists.id
                          AND lib_albums.removed_at IS NULL
                   )
                """
            )
            return int(conn.total_changes or 0)
    except Exception as e:
        logger.warning("prune_orphan_artists failed (table may not exist yet): %s", e)
        return 0
