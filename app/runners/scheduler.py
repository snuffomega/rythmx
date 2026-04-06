"""
scheduler.py - background maintenance and scheduling thread.

Threading-based, same pattern used by SoulSync's wishlist/watchlist timers.

Active responsibilities:
  - Forge scheduled runs: New Music (nm_schedule_*) and Custom Discovery (fd_schedule_*)
  - Acquisition worker tick
  - Image cache warming during idle hours
"""
import threading
import logging
from datetime import datetime
from app.db import rythmx_store
from app.runners import scheduler_helpers as _scheduler_helpers

logger = logging.getLogger(__name__)

# Module-level state
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def get_status() -> dict:
    return {
        "running": _thread is not None and _thread.is_alive(),
    }


def _should_library_sync(settings: dict) -> bool:
    return _scheduler_helpers.should_library_sync(settings)


def _loop():
    """Background loop - checks every hour for scheduled Forge runs."""
    while not _stop_event.is_set():
        settings = rythmx_store.get_all_settings()

        ran_forge = _scheduler_helpers.run_forge_scheduler_tick(
            settings=settings,
            store=rythmx_store,
            logger=logger,
        )

        _scheduler_helpers.run_acquisition_worker(logger)

        if not ran_forge:
            _scheduler_helpers.warm_image_cache(logger)
        _stop_event.wait(timeout=3600)  # Check every hour


def start():
    """Start the background scheduler thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="maintenance-scheduler")
    _thread.start()
    logger.info("Background scheduler started (Forge schedules + acquisition/image warmer active)")


def stop():
    """Signal the background thread to stop."""
    _stop_event.set()
    logger.info("Background scheduler stop requested")
