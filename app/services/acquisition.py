"""
acquisition.py — Provider-agnostic acquisition worker.

Drains the download_queue table in cc.db. Submission is stubbed until a
downloader (SoulSync watchlist, Lidarr, Slskd, etc.) is wired in.

To add a downloader later:
  1. Implement _submit_item() to POST to the downloader API
  2. Update status to 'submitted' after successful submission
  3. The re-check loop (_recheck_submitted) will flip 'submitted' → 'found'
     once the owned-check confirms the album is in the library

check_queue() is called by scheduler._loop() after each CC cycle.
It can also be called on-demand from main.py (e.g. via a future
POST /api/acquisition/check-now route).
"""
import logging
from datetime import datetime, timedelta
from app.db import cc_store

logger = logging.getLogger(__name__)

# How many days before a 'submitted' item that never resolves is marked 'failed'
_TIMEOUT_DAYS_DEFAULT = 30


def check_queue():
    """
    Single pass over the download_queue:
      1. Log all 'pending' items (stub — no downloader yet)
      2. Re-check 'submitted' items against SoulSync library; mark 'found' if present
      3. Mark 'submitted' items older than timeout_days as 'failed'
    """
    settings = cc_store.get_all_settings()
    timeout_days = int(settings.get("cc_acquisition_timeout_days", _TIMEOUT_DAYS_DEFAULT))

    # --- Step 1: pending items (stub) ---
    pending = cc_store.get_queue(status="pending")
    if pending:
        logger.info("Acquisition: %d item(s) pending (downloader not yet wired)", len(pending))
        for item in pending:
            _submit_item(item)

    # --- Step 2: re-check submitted items ---
    submitted = cc_store.get_queue(status="submitted")
    if submitted:
        _recheck_submitted(submitted)

    # --- Step 3: timeout stale submitted items ---
    cutoff = datetime.utcnow() - timedelta(days=timeout_days)
    for item in submitted:
        created = item.get("created_at") or ""
        try:
            created_dt = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            continue
        if created_dt < cutoff:
            cc_store.update_queue_status(item["id"], "failed",
                                         provider_response="timeout")
            logger.info("Acquisition: timed out '%s — %s' after %d days",
                        item["artist_name"], item["album_title"], timeout_days)


def _submit_item(queue_row: dict):
    """
    Stub submission. When a downloader is wired, POST to its API here and
    call cc_store.update_queue_status(queue_row['id'], 'submitted').

    Current behaviour: log only; leave status as 'pending'.
    """
    logger.debug(
        "Acquisition stub: would submit '%s \u2014 %s' (id=%d, source=%s)",
        queue_row["artist_name"], queue_row["album_title"],
        queue_row["id"], queue_row.get("source", "?"),
    )
    # Future:
    # response = downloader_api.request(queue_row)
    # cc_store.update_queue_status(queue_row['id'], 'submitted', provider_response=str(response))


def _recheck_submitted(items: list[dict]):
    """
    For each 'submitted' item, run a SoulSync owned-check.
    If the album is now in the library → mark 'found'.
    """
    from app.db import get_library_reader
    try:
        reader = get_library_reader()
    except Exception as e:
        logger.warning("Acquisition re-check: could not open library reader: %s", e)
        return

    for item in items:
        artist = item["artist_name"]
        album = item["album_title"]
        try:
            cached = cc_store.get_cached_artist(artist) or {}
            ss_id = cached.get("soulsync_artist_id") or reader.get_soulsync_artist_id(artist)
            rating_key = reader.check_album_owned(
                artist, album,
                soulsync_artist_id=ss_id,
                itunes_album_id=item.get("itunes_album_id"),
                deezer_album_id=item.get("deezer_album_id"),
                spotify_album_id=item.get("spotify_album_id"),
            )
            if rating_key:
                cc_store.update_queue_status(item["id"], "found",
                                             provider_response=f"rating_key={rating_key}")
                logger.info("Acquisition: found '%s \u2014 %s' in library (rating_key=%s)",
                            artist, album, rating_key)
        except Exception as e:
            logger.warning("Acquisition re-check error for '%s \u2014 %s': %s", artist, album, e)
