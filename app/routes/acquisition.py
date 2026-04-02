import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.db import rythmx_store
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/acquisition/queue")
def acquisition_queue_get(
    status: Optional[str] = None,
    playlist: Optional[str] = None,
):
    rows = rythmx_store.get_queue(status=status, playlist_name=playlist)
    items = [
        {
            "id": r.get("id"),
            "artist": r.get("artist_name", ""),
            "album": r.get("album_title", ""),
            "kind": r.get("kind", "album"),
            "status": r.get("status", "pending"),
            "requested_by": r.get("requested_by"),
            "requested_at": r.get("created_at"),
            "release_date": r.get("release_date"),
        }
        for r in rows
    ]
    return {"status": "ok", "items": items}


@router.post("/acquisition/queue")
def acquisition_queue_add(data: Optional[dict[str, Any]] = Body(default=None)):
    data = data or {}
    # Backward-compatible contract: accept both canonical backend keys
    # (artist_name/album_title) and older frontend keys (artist/album).
    artist = str(data.get("artist_name") or data.get("artist") or "").strip()
    album = str(data.get("album_title") or data.get("album") or "").strip()
    if not artist or not album:
        return JSONResponse(
            {
                "status": "error",
                "message": "artist_name/album_title (or artist/album) required",
            },
            status_code=400,
        )
    queue_id = rythmx_store.add_to_queue(
        artist_name=artist,
        album_title=album,
        release_date=data.get("release_date"),
        kind=data.get("kind"),
        source=data.get("source"),
        requested_by="manual",
    )
    return {"status": "ok", "queue_id": queue_id}


@router.get("/acquisition/stats")
def acquisition_stats():
    stats = rythmx_store.get_queue_stats()
    return {"status": "ok", **stats}


@router.post("/acquisition/check-now")
def acquisition_check_now():
    """Trigger the acquisition worker immediately (re-check submitted items)."""
    try:
        from app.services import acquisition
        acquisition.check_queue()
        stats = rythmx_store.get_queue_stats()
        return {"status": "ok", "message": "Acquisition worker run complete", **stats}
    except Exception as e:
        logger.warning("acquisition check-now failed: %s", e)
        return JSONResponse(
            {"status": "error", "message": "Acquisition check failed"}, status_code=500
        )
