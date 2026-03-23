"""
ownership_sync.py — Bulk-set is_owned on lib_releases by joining against lib_albums.

Three passes:
  Pass 1 — ID match (pure SQL): itunes_album_id, deezer_album_id, spotify_album_id
  Pass 2 — Title fuzzy match (Python): remaining unowned, match_album_title >= 0.82
  Pass 3 — Timestamp all unchecked rows
"""
import logging

from app.db.rythmx_store import _connect
from app.services.enrichment._helpers import match_ownership_title

logger = logging.getLogger(__name__)


def sync_release_ownership(conn=None) -> dict:
    """
    Bulk-set is_owned on lib_releases by joining against lib_albums.
    Returns {owned_by_id: int, owned_by_title: int, total_checked: int}.
    """
    close_conn = False
    if conn is None:
        conn = _connect()
        close_conn = True

    try:
        # Pass 1 — ID match (pure SQL)
        cursor = conn.execute(
            """
            UPDATE lib_releases SET is_owned = 1, owned_checked_at = datetime('now')
            WHERE is_owned = 0
              AND (
                (itunes_album_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM lib_albums la
                    WHERE la.itunes_album_id = lib_releases.itunes_album_id
                      AND la.removed_at IS NULL))
                OR (deezer_album_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM lib_albums la
                    WHERE la.deezer_id = lib_releases.deezer_album_id
                      AND la.removed_at IS NULL))
                OR (spotify_album_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM lib_albums la
                    WHERE la.spotify_album_id = lib_releases.spotify_album_id
                      AND la.removed_at IS NULL))
              )
            """
        )
        owned_by_id = cursor.rowcount

        # Pass 2 — Title fuzzy match (Python, remaining unowned only)
        owned_by_title = 0
        unowned = conn.execute(
            """
            SELECT lr.id, lr.artist_id, lr.title
            FROM lib_releases lr
            WHERE lr.is_owned = 0 AND lr.owned_checked_at IS NULL
            """
        ).fetchall()

        for release in unowned:
            lib_albums = conn.execute(
                """
                SELECT title, local_title FROM lib_albums
                WHERE artist_id = ? AND removed_at IS NULL
                """,
                (release["artist_id"],),
            ).fetchall()

            matched = False
            for la in lib_albums:
                lib_title = la["local_title"] or la["title"]
                if match_ownership_title(release["title"], lib_title):
                    matched = True
                    break

            if matched:
                conn.execute(
                    "UPDATE lib_releases SET is_owned = 1, owned_checked_at = datetime('now') WHERE id = ?",
                    (release["id"],),
                )
                owned_by_title += 1

        # Pass 3 — Timestamp all unchecked
        cursor3 = conn.execute(
            "UPDATE lib_releases SET owned_checked_at = datetime('now') WHERE owned_checked_at IS NULL"
        )
        total_checked = owned_by_id + owned_by_title + cursor3.rowcount

        conn.commit()

        logger.info(
            "sync_release_ownership: owned_by_id=%d owned_by_title=%d total_checked=%d",
            owned_by_id, owned_by_title, total_checked,
        )

        return {
            "owned_by_id": owned_by_id,
            "owned_by_title": owned_by_title,
            "total_checked": total_checked,
        }
    finally:
        if close_conn:
            conn.close()
