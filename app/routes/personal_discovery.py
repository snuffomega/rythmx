from flask import Blueprint, jsonify

personal_discovery_bp = Blueprint("personal_discovery", __name__)


@personal_discovery_bp.route("/api/personal-discovery/run", methods=["POST"])
def personal_discovery_run():
    """Stub endpoint — Personal Discovery engine not yet implemented."""
    return jsonify([])
