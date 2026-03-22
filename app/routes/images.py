from typing import Any, Optional

from fastapi import APIRouter, Body, Depends

from app.dependencies import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


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


@router.post("/images/warm-cache")
def warm_cache(data: Optional[dict[str, Any]] = Body(default=None)):
    from app.services import image_service
    data = data or {}
    try:
        max_items = min(int(data.get("max_items", 40)), 100)
    except (TypeError, ValueError):
        max_items = 40
    submitted = image_service.warm_image_cache(max_items=max_items)
    return {"status": "ok", "submitted": submitted}
