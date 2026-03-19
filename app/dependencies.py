"""
dependencies.py — FastAPI shared dependencies.

API key verification is applied to all /api/v1/ routes except /auth/bootstrap.
Use Depends(verify_api_key) on any router that requires authentication.
"""
import hmac
import logging

from fastapi import Header, HTTPException

from app.db import rythmx_store

logger = logging.getLogger(__name__)


def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """
    FastAPI dependency — validates X-Api-Key header against the stored key.

    Raises HTTP 401 if the key is missing or incorrect.
    Injected into all /api/v1/ routers except auth (which is public by design).
    """
    stored = rythmx_store.get_api_key() or ""
    if not hmac.compare_digest(x_api_key, stored):
        raise HTTPException(
            status_code=401,
            detail={"status": "error", "message": "Unauthorized"},
        )
