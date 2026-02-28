from flask import Blueprint, jsonify, request

images_bp = Blueprint("images", __name__)


@images_bp.route("/api/images/resolve", methods=["POST"])
def images_resolve():
    from app import image_service
    body = request.get_json(silent=True) or {}
    entity_type = body.get("type", "")
    name = body.get("name", "")
    artist = body.get("artist", "")
    if not entity_type or not name:
        return jsonify({"image_url": ""})
    url, pending = image_service.resolve_image(entity_type, name, artist)
    return jsonify({"image_url": url, "pending": pending})
