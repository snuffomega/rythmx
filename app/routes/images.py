from typing import Any, Optional
import logging

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.dependencies import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)


@router.post("/images/resolve")
def images_resolve(body: Optional[dict[str, Any]] = Body(default=None)):
    from app.services import image_service
    body = body or {}
    entity_type = body.get("type", "")
    name = body.get("name", "")
    artist = body.get("artist", "")
    if not entity_type or not name:
        return {"image_url": ""}
    url, pending = image_service.resolve_image(entity_type, name, artist)
    return {"image_url": url, "pending": pending}


@router.post("/images/resolve-batch")
def images_resolve_batch(body: Optional[dict[str, Any]] = Body(default=None)):
    """
    Resolve many image requests in one round-trip.

    Body:
      {
        "items": [
          { "id": "cache-key", "type": "artist|album|track", "name": "...", "artist": "..." }
        ]
      }
    """
    from app.services import image_service

    body = body or {}
    items = body.get("items", [])
    if not isinstance(items, list):
        return JSONResponse(
            {"status": "error", "message": "items must be a list"},
            status_code=400,
        )

    results: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue

        item_id = str(raw.get("id", ""))
        entity_type = str(raw.get("type", "")).strip()
        name = str(raw.get("name", "")).strip()
        artist = str(raw.get("artist", "")).strip()

        if not entity_type or not name:
            results.append({"id": item_id, "image_url": "", "pending": False})
            continue

        image_url, pending = image_service.resolve_image(entity_type, name, artist)
        results.append({"id": item_id, "image_url": image_url, "pending": pending})

    logger.info("Resolved %d images.", len(results))
    return {"items": results}



