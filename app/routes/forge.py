"""
forge.py — The Forge pipeline-history endpoint.

All SQL uses ? placeholders. Router registered at /api/v1 in main.py.
"""
import logging

from fastapi import APIRouter, Depends, Query

from app.db import rythmx_store
from app.dependencies import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/forge/pipeline-history")
def get_pipeline_history(
    pipeline_type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """Return recent pipeline_history rows, optionally filtered by pipeline_type."""
    runs = rythmx_store.get_pipeline_runs(pipeline_type=pipeline_type, limit=limit)
    return {"status": "ok", "runs": runs}
