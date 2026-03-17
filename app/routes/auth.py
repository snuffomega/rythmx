"""
auth.py — Public authentication bootstrap endpoint.

GET /auth/bootstrap  — returns the API key so the web UI can seed its
  X-Api-Key header without user intervention. No authentication required.

Why public? This endpoint is safe to leave unauthenticated because:
  - Rythmx is a self-hosted, LAN-only tool by design.
  - The web UI is served from the same origin as the API (same Flask server).
  - The key protects the v1 API from external tool access; it does not prevent
    a browser user who can already load the web UI from seeing their own key.
  - For internet-exposed deployments, place a reverse proxy with its own auth
    in front of the entire service (standard self-hosted practice).
"""
import logging
from flask import Blueprint, jsonify
from app.db import rythmx_store

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/auth/bootstrap", methods=["GET"])
def bootstrap():
    """Return the active API key for the web UI to bootstrap its X-Api-Key header."""
    key = rythmx_store.get_api_key()
    if not key:
        logger.error("Bootstrap called but no API key exists in the database")
        return jsonify({"status": "error", "message": "API key not initialized"}), 503
    return jsonify({"status": "ok", "api_key": key})
