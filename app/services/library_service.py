"""
library_service.py — Backward-compatible facade.

All enrichment logic has been decomposed into app.services.enrichment.*
This facade re-exports every public name so existing callers
(settings.py, api_orchestrator.py, scheduler.py, main.py) resolve unchanged.
"""
from app.services.enrichment import *  # noqa: F401,F403

# Re-export _pipeline_running as a function for thread safety.
# scheduler.py previously imported the bare bool; it now uses is_pipeline_running().
# Keep this alias so any stale reference still works at import time.
from app.services.enrichment.pipeline import is_pipeline_running as is_pipeline_running  # noqa: F401
