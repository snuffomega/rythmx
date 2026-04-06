"""
Forge pipeline history routes.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/forge/pipeline-history")
def get_pipeline_history(
    pipeline_type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """Return recent pipeline_history rows, optionally filtered by pipeline_type."""
    from app.routes import forge as facade

    runs = facade.rythmx_store.get_pipeline_runs(pipeline_type=pipeline_type, limit=limit)
    return {"status": "ok", "runs": runs}

