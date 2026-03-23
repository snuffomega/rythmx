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



