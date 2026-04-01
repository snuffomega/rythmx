"""
library_playlists.py — Platform playlist routes (Navidrome + Plex).

Manages lib_playlists and lib_playlist_tracks tables.
Forge-generated playlists live in forge_playlists — not touched here.

All routes require X-Api-Key via Depends(verify_api_key).
Router registered at /api/v1 in main.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import rythmx_store
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class RenameBody(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# List all playlists
# ---------------------------------------------------------------------------

@router.get("/library/playlists")
def list_playlists():
    """Return all lib_playlists ordered by name."""
    with rythmx_store._connect() as conn:
        rows = conn.execute(
            "SELECT id, name, source_platform, cover_url, track_count, "
            "duration_ms, updated_at, synced_at "
            "FROM lib_playlists ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return {
        "status": "ok",
        "playlists": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# Tracks for a playlist
# ---------------------------------------------------------------------------

@router.get("/library/playlists/{playlist_id}/tracks")
def get_playlist_tracks(playlist_id: str):
    """Return tracks for a playlist joined with lib_tracks, ordered by position."""
    with rythmx_store._connect() as conn:
        pl = conn.execute(
            "SELECT id FROM lib_playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if not pl:
            raise HTTPException(
                status_code=404,
                detail={"status": "error", "message": "Playlist not found"},
            )

        rows = conn.execute(
            """
            SELECT
                lpt.position,
                lpt.track_id,
                t.title,
                a.name  AS artist_name,
                al.title AS album_title,
                t.duration,
                t.file_path
            FROM lib_playlist_tracks lpt
            JOIN lib_tracks t  ON t.id = lpt.track_id
            LEFT JOIN lib_artists a  ON a.id = t.artist_id
            LEFT JOIN lib_albums  al ON al.id = t.album_id
            WHERE lpt.playlist_id = ?
            ORDER BY lpt.position
            """,
            (playlist_id,),
        ).fetchall()

    return {
        "status": "ok",
        "tracks": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@router.post("/library/playlists/sync")
def sync_playlists():
    """Trigger a full platform playlist sync into lib_playlists."""
    try:
        from app.services.library_playlists_service import sync_playlists as _sync
        result = _sync()
        return {"status": "ok", **result}
    except ValueError as exc:
        logger.warning("library_playlists sync error: %s", exc)
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("library_playlists sync failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Playlist sync failed"},
        )


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

@router.patch("/library/playlists/{playlist_id}")
def rename_playlist(playlist_id: str, body: RenameBody):
    """Rename a playlist on the platform and update lib_playlists."""
    if not body.name.strip():
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "name must not be blank"},
        )
    try:
        from app.services.library_playlists_service import rename_playlist as _rename
        _rename(playlist_id, body.name.strip())
        return {"status": "ok", "id": playlist_id, "name": body.name.strip()}
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("rename_playlist failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Rename failed"},
        )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/library/playlists/{playlist_id}")
def delete_playlist(playlist_id: str):
    """Delete a playlist from the platform and from lib_playlists."""
    try:
        from app.services.library_playlists_service import delete_playlist as _delete
        _delete(playlist_id)
        return {"status": "ok", "id": playlist_id}
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("delete_playlist failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "Delete failed"},
        )
