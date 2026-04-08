"""
tidarr_poller.py — completion detection and post-download pipeline for Tidarr jobs.

Polls Tidarr's SABnzbd-compatible history endpoint and reconciles results
against the `download_jobs` table in rythmx.db.

Called by the background scheduler every 60 seconds via _poll_loop().

Flow:
  1. Read all pending tidarr jobs from DB.
  2. If none, return early (no-op).
  3. Call downloader.poll_history() — Tidarr SABnzbd history endpoint.
  4. For each history slot whose nzo_id is in our pending set:
       Completed → mark completed + store the Tidarr-internal `storage` path
                 → call _run_post_download_pipeline() to:
                     a. translate the storage path via downloader.translate_path()
                     b. glob FLACs from the local path
                     c. pass DownloadArtifact through tagger then file_handler
                     d. update storage_path with library dest if mover ran
       Failed    → mark failed
  5. Unmatched pending jobs are left as-is (still downloading or queued).
"""
import glob as _glob
import logging
import os

from app import plugins as _plugins
from app.db import rythmx_store
from app.plugins import DownloadArtifact

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

    # Build lookup so the file handler can access artist/album without a second DB query.
    pending_by_id = {job["job_id"]: job for job in pending}
    pending_ids = set(pending_by_id.keys())
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

            # Post-download pipeline: translate path, glob FLACs, tag, move.
            # Only invoked when storage is present; pipeline handles all noop cases.
            if storage:
                _run_post_download_pipeline(nzo_id, storage, pending_by_id[nzo_id])

        elif status == "failed":
            rythmx_store.update_download_job_status(nzo_id, "failed")
            logger.warning("tidarr_poller: job FAILED  nzo_id=%s", nzo_id)
            result["failed"] += 1
            matched.add(nzo_id)

    result["skipped"] = len(pending_ids - matched)
    return result


def _run_post_download_pipeline(nzo_id: str, storage_path: str, job: dict) -> None:
    """
    Execute the full post-download plugin pipeline for one completed job.

    Sequence:
      1. downloader.translate_path(storage_path) — plugin translates its internal
         path to the locally accessible path (identity if volumes are wired correctly).
      2. Core globs *.flac from the translated directory — plugins never need to know
         how to find files; they receive a DownloadArtifact with files already populated.
      3. tagger.tag(artifact) — noop if no tagger plugin is loaded.
      4. file_handler.organize(artifact) — noop if no file_handler plugin is loaded.
      5. Update download_jobs.storage_path with artifact.dest_dir if it was set.

    Errors in any plugin stage are caught and logged — never propagated.
    """
    downloader = _plugins.get_downloader()

    # 1. Path translation (plugin's concern — Core passes raw Tidarr storage path)
    translate = getattr(downloader, "translate_path", lambda p: p)
    local_path = translate(storage_path)

    # 2. File discovery (Core's concern — plugins receive a clean file list)
    if not os.path.isdir(local_path):
        logger.warning(
            "tidarr_poller: source dir not accessible: %s (storage_path=%s)",
            local_path, storage_path,
        )
        return

    flac_files = sorted(_glob.glob(os.path.join(local_path, "**", "*.flac"), recursive=True))
    if not flac_files:
        logger.warning("tidarr_poller: no FLACs found in %s", local_path)
        return

    artifact = DownloadArtifact(
        job_id=nzo_id,
        artist=job.get("artist_name") or "",
        album=job.get("album_name") or "",
        source_dir=local_path,
        files=flac_files,
        metadata={
            "artist": job.get("artist_name") or "",
            "album":  job.get("album_name") or "",
        },
    )

    logger.info(
        "tidarr_poller: pipeline start  nzo_id=%s  files=%d  source=%s",
        nzo_id, len(flac_files), local_path,
    )

    # 3. Tag
    tagger = _plugins.get_tagger()
    if tagger.name != "noop":
        try:
            artifact = tagger.tag(artifact)
        except Exception as exc:
            logger.warning("tidarr_poller: tagger error nzo_id=%s: %s", nzo_id, exc)

    # 4. Move / organize
    file_handler = _plugins.get_file_handler()
    if file_handler.name != "noop":
        try:
            artifact = file_handler.organize(artifact)
        except Exception as exc:
            logger.warning("tidarr_poller: file_handler error nzo_id=%s: %s", nzo_id, exc)

    # 5. Persist library destination
    if artifact.dest_dir and artifact.dest_dir != storage_path:
        rythmx_store.update_download_job_status(
            nzo_id, "completed", storage_path=artifact.dest_dir
        )
        logger.info(
            "tidarr_poller: pipeline complete  nzo_id=%s  dest=%s",
            nzo_id, artifact.dest_dir,
        )
