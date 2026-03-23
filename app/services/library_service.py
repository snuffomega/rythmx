"""
library_service.py — Backward-compatible facade.

All enrichment logic has been decomposed into app.services.enrichment.*
This facade re-exports every public name so existing callers
(settings.py, api_orchestrator.py, scheduler.py, main.py) resolve unchanged.
"""
from app.services.enrichment import *  # noqa: F401,F403
