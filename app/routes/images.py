from flask import Blueprint, jsonify, request

images_bp = Blueprint("images", __name__)


@images_bp.route("/api/images/resolve", methods=["POST"])
def images_resolve():
    from app.services import image_service
    body = request.get_json(silent=True) or {}
    entity_type = body.get("type", "")
    name = body.get("name", "")
    artist = body.get("artist", "")
    if not entity_type or not name:
        return jsonify({"image_url": ""})
    url, pending = image_service.resolve_image(entity_type, name, artist)
    return jsonify({"image_url": url, "pending": pending})


@images_bp.route("/api/images/warm-cache", methods=["POST"])
def warm_cache():
    from app.services import image_service
    data = request.get_json(silent=True) or {}
    try:
        max_items = min(int(data.get("max_items", 40)), 100)
    except (TypeError, ValueError):
        max_items = 40
    submitted = image_service.warm_image_cache(max_items=max_items)
    return jsonify({"status": "ok", "submitted": submitted})
