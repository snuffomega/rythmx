"""
pipeline.py — Full automated library pipeline orchestrator.

Thread-safe guard via threading.Lock (replaces bare bool from library_service.py).
"""
import concurrent.futures
import logging
import threading
from datetime import datetime

from app.db import rythmx_store

logger = logging.getLogger(__name__)

_pipeline_lock = threading.Lock()
_pipeline_running = False


def is_pipeline_running() -> bool:
    """Thread-safe check — use this instead of reading the bare bool."""
    return _pipeline_running


def run_auto_pipeline() -> dict:
    """
    Full automated library pipeline: sync → enrich IDs → tags + bonus Spotify → BPM.

    Phase 1  — sync_library() (uncapped — always full library scan)
    Phase 1b — loop enrich_library(50) until remaining=0 or lib_enrich_ids_batch processed
    Phase 2a — enrich_lastfm_tags() always-on              } parallel via
    Phase 2b — enrich_spotify()     bonus only              } ThreadPoolExecutor
    Phase 3  — enrich_deezer_bpm()  capped at lib_enrich_bpm_batch tracks per run

    All stages are resumable via enrichment_meta. Returns a summary dict.
    """
    global _pipeline_running

    with _pipeline_lock:
        if _pipeline_running:
            logger.info("run_auto_pipeline: already running — skipping")
            return {"status": "skipped", "reason": "already_running"}
        _pipeline_running = True

    logger.info("run_auto_pipeline: starting")
    result: dict = {"status": "ok"}

    try:
        from app.services.enrichment.sync import sync_library
        from app.services.enrichment.id_itunes_deezer import enrich_library
        from app.services.enrichment.tags_lastfm import enrich_lastfm_tags
        from app.services.enrichment.id_spotify import enrich_spotify
        from app.services.enrichment.bpm_deezer import enrich_deezer_bpm

        settings = rythmx_store.get_all_settings()

        def _bool(key: str, default: bool = True) -> bool:
            v = settings.get(key)
            if v is None:
                return default
            return str(v).lower() not in ("false", "0", "no")

        def _int(key: str, default: int) -> int:
            try:
                return int(settings.get(key, default))
            except (TypeError, ValueError):
                return default

        # ---- Phase 1: Full library sync (uncapped) ----
        sync_result = sync_library()
        result["sync"] = sync_result
        logger.info(
            "run_auto_pipeline: sync complete — artists=%d albums=%d tracks=%d",
            sync_result.get("artist_count", 0),
            sync_result.get("album_count", 0),
            sync_result.get("track_count", 0),
        )

        # ---- Phase 1b: ID enrichment loop (trickle-capped per run) ----
        if _bool("lib_enrich_ids"):
            batch_size = 50
            per_run_cap = _int("lib_enrich_ids_batch", 500)
            processed_this_run = 0
            total_enriched = 0
            total_failed = 0
            consecutive_no_progress = 0
            remaining = -1
            while processed_this_run < per_run_cap:
                r = enrich_library(batch_size=min(batch_size, per_run_cap - processed_this_run))
                batch_enriched = r.get("enriched", 0)
                batch_skipped = r.get("skipped", 0)
                batch_failed = r.get("failed", 0)
                batch_processed = batch_enriched + batch_skipped + batch_failed
                total_enriched += batch_enriched
                total_failed += batch_failed
                remaining = r.get("remaining", 0)
                if batch_processed == 0:
                    break  # nothing fetched — all done or all excluded by enrichment_meta
                # Bail if no albums are actually being enriched (all skipped/failed)
                if batch_enriched == 0:
                    consecutive_no_progress += 1
                    if consecutive_no_progress >= 2:
                        logger.info(
                            "run_auto_pipeline: ID enrichment — no progress for 2 batches, "
                            "remaining=%d likely blocked by cooldown", remaining,
                        )
                        break
                else:
                    consecutive_no_progress = 0
                processed_this_run += batch_processed
            result["enrich_ids"] = {
                "enriched": total_enriched,
                "failed": total_failed,
                "processed_this_run": processed_this_run,
                "remaining": remaining,
            }
            logger.info(
                "run_auto_pipeline: ID enrichment — enriched=%d failed=%d processed=%d remaining=%d",
                total_enriched, total_failed, processed_this_run, remaining,
            )

        # ---- Phase 1.5: Ownership sync on lib_releases ----
        from app.services.enrichment.ownership_sync import sync_release_ownership
        try:
            own_result = sync_release_ownership()
            result["ownership_sync"] = own_result
            logger.info(
                "run_auto_pipeline: ownership sync — id=%d title=%d",
                own_result.get("owned_by_id", 0),
                own_result.get("owned_by_title", 0),
            )
        except Exception as e:
            logger.warning("run_auto_pipeline: ownership sync failed: %s", e)

        # ---- Phase 1.6: Refresh missing_count on lib_artists ----
        from app.db.rythmx_store import refresh_missing_counts
        try:
            refresh_missing_counts()
            logger.info("run_auto_pipeline: missing_count refresh complete")
        except Exception as e:
            logger.warning("run_auto_pipeline: missing_count refresh failed: %s", e)

        # ---- Phase 1.7: Refresh canonical_release_id groupings ----
        from app.db.rythmx_store import populate_canonical_release_ids
        try:
            canonical_updated = populate_canonical_release_ids()
            result["canonical_refresh"] = canonical_updated
            logger.info("run_auto_pipeline: canonical refresh — %d rows", canonical_updated)
        except Exception as e:
            logger.warning("run_auto_pipeline: canonical refresh failed: %s", e)

        # ---- Phase 2: Last.fm tags (always-on) + bonus Spotify (parallel) ----
        phase2_tasks: dict[str, concurrent.futures.Future] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="lib-enrich"
        ) as pool:
            if _bool("lib_enrich_lastfm"):
                lastfm_batch = _int("lib_enrich_lastfm_batch", 100)
                phase2_tasks["lastfm"] = pool.submit(enrich_lastfm_tags, lastfm_batch)
            if _bool("lib_enrich_spotify"):
                spotify_batch = _int("lib_enrich_spotify_batch", 50)
                phase2_tasks["spotify"] = pool.submit(enrich_spotify, spotify_batch)

        for key, future in phase2_tasks.items():
            try:
                result[f"enrich_{key}"] = future.result()
            except Exception as e:
                logger.warning("run_auto_pipeline: phase 2 %s raised: %s", key, e)
                result[f"enrich_{key}"] = {"status": "error", "error": str(e)}

        # ---- Phase 3: Deezer BPM (requires deezer_id from Phase 1) ----
        if _bool("lib_enrich_bpm"):
            bpm_batch = _int("lib_enrich_bpm_batch", 200)
            bpm_result = enrich_deezer_bpm(batch_size=bpm_batch)
            result["enrich_bpm"] = bpm_result
            logger.info(
                "run_auto_pipeline: BPM — tracks=%d albums=%d failed=%d",
                bpm_result.get("enriched_tracks", 0),
                bpm_result.get("enriched_albums", 0),
                bpm_result.get("failed", 0),
            )

        rythmx_store.set_setting("library_last_synced", datetime.utcnow().isoformat())
        logger.info("run_auto_pipeline: complete")

    except Exception as e:
        logger.exception("run_auto_pipeline: unhandled error: %s", e)
        result["status"] = "error"
        result["error"] = str(e)
    finally:
        _pipeline_running = False

    return result
