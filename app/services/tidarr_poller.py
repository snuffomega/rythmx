"""
tidarr_poller.py — Step 4b: completion detection for Tidarr download jobs.

Polls Tidarr's SABnzbd-compatible history endpoint and reconciles results
against the `download_jobs` table in rythmx.db.

Called by the background scheduler every 60 seconds via _poll_loop().

Flow:
  1. Read all pending tidarr jobs from DB.
  2. If none, return early (no-op).
  3. Call downloader.poll_history() — Tidarr SABnzbd history endpoint.
  4. For each history slot whose nzo_id is in our pending set:
       Completed → mark completed + store the `storage` path (final folder on host)
       Failed    → mark failed
  5. Unmatched pending jobs are left as-is (still downloading or queued).

The `storage` path returned by Tidarr is the absolute path on the Tidarr
container/host (e.g. /music/Daft Punk/2013 - Random Access Memories).
Step 5 (file_mover) will use this path once it is implemented.
"""
import logging

from app import plugins as _plugins
from app.db import rythmx_store

logger = logging.getLogger(__name__)


def poll_once() -> dict:
    """
    Execute one polling cycle.

    Returns:
        {
            "checked":   int  — number of pending jobs examined,
            "completed": int  — jobs moved to 'completed',
            "failed":    int  — jobs moved to 'failed',
            "skipped":   int  — pending jobs not yet visible in history,
        }
    """
    pending = rythmx_store.get_pending_download_jobs(provider="tidarr")
    if not pending:
        return {"checked": 0, "completed": 0, "failed": 0, "skipped": 0}

    downloader = _plugins.get_downloader()
    if not hasattr(downloader, "poll_history"):
        # Active downloader plugin does not support polling (e.g. StubDownloader).
        logger.debug("tidarr_poller: downloader has no poll_history — skipping")
        return {"checked": 0, "completed": 0, "failed": 0, "skipped": len(pending)}

    pending_ids = {job["job_id"] for job in pending}
    result = {"checked": len(pending), "completed": 0, "failed": 0, "skipped": 0}

    history_slots = downloader.poll_history(limit=200)
    matched: set[str] = set()

    for slot in history_slots:
        nzo_id = slot.get("nzo_id") or ""
        if nzo_id not in pending_ids:
            continue

        status = (slot.get("status") or "").lower()
        storage: str | None = slot.get("storage") or None

        if status == "completed":
            rythmx_store.update_download_job_status(nzo_id, "completed", storage_path=storage)
            logger.info(
                "tidarr_poller: job completed  nzo_id=%s  storage=%s",
                nzo_id,
                storage,
            )
            result["completed"] += 1
            matched.add(nzo_id)

        elif status == "failed":
            rythmx_store.update_download_job_status(nzo_id, "failed")
            logger.warning("tidarr_poller: job FAILED  nzo_id=%s", nzo_id)
            result["failed"] += 1
            matched.add(nzo_id)

    result["skipped"] = len(pending_ids - matched)
    return result
