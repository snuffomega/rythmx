"""
runner.py — PipelineRunner: single control plane for the enrichment pipeline.

Owns stage ordering, DAG parallelism, heartbeat, and stale-lock recovery.
Does NOT own WS broadcasting, timer scheduling, or connection verification —
those are caller concerns injected via callbacks.

Replaces both pipeline.py (headless auto-run) and the stage sequencing in
EnrichmentOrchestrator._run() (manual UI-triggered run).
"""
import concurrent.futures
import logging
import threading
from datetime import datetime

from app.db import rythmx_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB-persisted pipeline state keys (stored in app_settings)
# ---------------------------------------------------------------------------
_KEY_STARTED = "pipeline_started_at"
_KEY_HEARTBEAT = "pipeline_heartbeat"
_KEY_PHASE = "pipeline_phase"

HEARTBEAT_INTERVAL = 15   # seconds
STALE_THRESHOLD = 60      # seconds — heartbeat older than this = crashed


class PipelineRunner:
    """Single control plane for the enrichment pipeline.

    Callers provide:
      - batch_size: how many entities per stage batch
      - stop_event: threading.Event for graceful cancellation
      - on_progress: factory fn(stage_key) -> callback(found, not_found, errors, total)
                     or None for headless operation
    """

    _lock = threading.Lock()

    def run(
        self,
        batch_size: int = 500,
        stop_event: threading.Event | None = None,
        on_progress: "callable | None" = None,
        on_phase: "callable | None" = None,
    ) -> dict:
        """Execute the full enrichment DAG. Returns summary dict."""
        if not self._lock.acquire(blocking=False):
            logger.info("PipelineRunner: already running — skipping")
            return {"status": "skipped", "reason": "already_running"}

        try:
            return self._execute(batch_size, stop_event, on_progress, on_phase)
        finally:
            self._clear_state()
            self._lock.release()

    @classmethod
    def is_running(cls) -> bool:
        """Thread-safe check using the class-level lock."""
        if cls._lock.acquire(blocking=False):
            cls._lock.release()
            return False
        return True

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    @staticmethod
    def _is_stale_lock() -> bool:
        """Check if a previous run crashed (heartbeat stopped updating)."""
        started = rythmx_store.get_setting(_KEY_STARTED)
        if not started:
            return False
        heartbeat = rythmx_store.get_setting(_KEY_HEARTBEAT)
        if not heartbeat:
            return True  # started but never heartbeated — crashed immediately
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(heartbeat)).total_seconds()
            return age > STALE_THRESHOLD
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _clear_state():
        """Remove all DB-persisted pipeline state."""
        for key in (_KEY_STARTED, _KEY_HEARTBEAT, _KEY_PHASE):
            rythmx_store.set_setting(key, "")

    @staticmethod
    def _set_phase(phase: str, on_phase: "callable | None" = None):
        rythmx_store.set_setting(_KEY_PHASE, phase)
        rythmx_store.set_setting(_KEY_HEARTBEAT, datetime.utcnow().isoformat())
        if on_phase:
            on_phase(phase)

    def _start_heartbeat(self, stop_event: threading.Event | None) -> threading.Event:
        """Start a background thread that updates the heartbeat timestamp."""
        cancel = threading.Event()

        def _beat():
            while not cancel.is_set():
                rythmx_store.set_setting(_KEY_HEARTBEAT, datetime.utcnow().isoformat())
                # Wait interruptibly — exits quickly on cancel or stop
                if cancel.wait(timeout=HEARTBEAT_INTERVAL):
                    break
                if stop_event and stop_event.is_set():
                    break

        t = threading.Thread(target=_beat, daemon=True, name="pipeline-heartbeat")
        t.start()
        return cancel

    # ------------------------------------------------------------------
    # Progress helper
    # ------------------------------------------------------------------

    @staticmethod
    def _progress_fn(on_progress, key: str):
        """Return a per-stage progress callback, or None if headless."""
        if on_progress is None:
            return None
        return on_progress(key)

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _stopped(self, stop_event: threading.Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()

    def _execute(
        self,
        batch_size: int,
        stop_event: threading.Event | None,
        on_progress: "callable | None",
        on_phase: "callable | None" = None,
    ) -> dict:
        # Prune stale lock from a crashed previous run
        if self._is_stale_lock():
            logger.warning("PipelineRunner: pruning stale lock (previous run crashed)")
            self._clear_state()

        # Mark start
        rythmx_store.set_setting(_KEY_STARTED, datetime.utcnow().isoformat())
        heartbeat_cancel = self._start_heartbeat(stop_event)

        result: dict = {"status": "ok"}

        try:
            # === Stage 1: Library Sync (DB only, fast) ===
            self._set_phase("sync", on_phase)
            try:
                from app.services.enrichment.sync import sync_library
                sync_result = sync_library()
                result["sync"] = sync_result
                logger.info(
                    "PipelineRunner: sync — artists=%d albums=%d tracks=%d",
                    sync_result.get("artist_count", 0),
                    sync_result.get("album_count", 0),
                    sync_result.get("track_count", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: sync failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                return result

            # === Stage 2a: iTunes/Deezer IDs (sequential — feeds catalog promotion) ===
            self._set_phase("id_itunes_deezer", on_phase)
            modified_artist_ids: list[str] = []
            try:
                from app.services.enrichment.id_itunes_deezer import enrich_library
                s2a_result = enrich_library(
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, "library"),
                )
                modified_artist_ids = s2a_result.get("modified_artist_ids", [])
            except Exception as e:
                logger.error("PipelineRunner: iTunes/Deezer IDs failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                return result

            # === Stage 2b: PARALLEL — Spotify IDs + Last.fm IDs + MusicBrainz IDs + Artist Artwork ===
            self._set_phase("id_parallel", on_phase)
            from app.services.enrichment.id_spotify import enrich_artist_ids_spotify
            from app.services.enrichment.id_lastfm import enrich_artist_ids_lastfm
            from app.services.enrichment.id_musicbrainz import enrich_artist_ids_musicbrainz
            from app.services.enrichment.art_artist import enrich_artist_art

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="stage2b"
            ) as pool:
                fut_spotify = pool.submit(
                    enrich_artist_ids_spotify,
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, "spotify_id"),
                )
                fut_lastfm = pool.submit(
                    enrich_artist_ids_lastfm,
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, "lastfm_id"),
                )

                # Wait for Last.fm only — artwork + MusicBrainz need MBID from Last.fm
                try:
                    fut_lastfm.result()
                except Exception as e:
                    logger.error("PipelineRunner: Last.fm IDs failed: %s", e)

                # Now submit artwork + MusicBrainz ID (both have MBID available)
                fut_art = pool.submit(
                    enrich_artist_art,
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, "artist_art"),
                )
                fut_musicbrainz = pool.submit(
                    enrich_artist_ids_musicbrainz,
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, "musicbrainz_id"),
                )

                # Wait for remaining parallel tasks
                for name, fut in [("Spotify IDs", fut_spotify), ("Artist Art", fut_art),
                                  ("MusicBrainz IDs", fut_musicbrainz)]:
                    try:
                        fut.result()
                    except Exception as e:
                        logger.error("PipelineRunner: %s failed: %s", name, e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                return result

            # === Ownership Chain (sequential — each reads previous output) ===

            # Ownership sync
            self._set_phase("ownership_sync", on_phase)
            try:
                from app.services.enrichment.ownership_sync import sync_release_ownership
                own_result = sync_release_ownership()
                result["ownership_sync"] = own_result
                logger.info(
                    "PipelineRunner: ownership sync — id=%d title=%d",
                    own_result.get("owned_by_id", 0),
                    own_result.get("owned_by_title", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: ownership sync failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                return result

            # Normalize titles (scoped to modified artists when available)
            self._set_phase("normalize_titles", on_phase)
            scope = modified_artist_ids or None
            try:
                from app.db.rythmx_store import recompute_normalized_titles
                recomputed = recompute_normalized_titles(artist_ids=scope)
                result["recompute_titles"] = recomputed
                logger.info("PipelineRunner: normalized_title recomputed for %d rows", recomputed)
            except Exception as e:
                logger.warning("PipelineRunner: normalized_title recompute failed: %s", e)

            # Missing counts (scoped to modified artists when available)
            self._set_phase("missing_counts", on_phase)
            try:
                from app.db.rythmx_store import refresh_missing_counts
                refresh_missing_counts(artist_ids=scope)
                logger.info("PipelineRunner: missing_count refresh complete")
            except Exception as e:
                logger.warning("PipelineRunner: missing_count refresh failed: %s", e)

            # Canonical grouping (scoped to modified artists when available)
            self._set_phase("canonical", on_phase)
            try:
                from app.db.rythmx_store import populate_canonical_release_ids
                canonical_updated = populate_canonical_release_ids(artist_ids=scope)
                result["canonical_refresh"] = canonical_updated
                logger.info("PipelineRunner: canonical refresh — %d rows", canonical_updated)
            except Exception as e:
                logger.warning("PipelineRunner: canonical refresh failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                return result

            # === Stage 3: Rich Data PARALLEL (8 workers, 4 threads) ===
            self._set_phase("rich_data", on_phase)
            from app.services.enrichment.rich_itunes import enrich_itunes_rich
            from app.services.enrichment.rich_deezer import enrich_deezer_release
            from app.services.enrichment.rich_spotify import enrich_genres_spotify
            from app.services.enrichment.tags_lastfm import enrich_tags_lastfm
            from app.services.enrichment.stats_lastfm import enrich_stats_lastfm
            from app.services.enrichment.rich_deezer_artist import enrich_deezer_artist
            from app.services.enrichment.rich_similar import enrich_similar_artists
            from app.services.enrichment.rich_musicbrainz import enrich_musicbrainz_rich

            stage3_workers = [
                (enrich_itunes_rich, "itunes_rich"),
                (enrich_deezer_release, "deezer_rich"),
                (enrich_genres_spotify, "spotify_genres"),
                (enrich_tags_lastfm, "lastfm_tags"),
                (enrich_stats_lastfm, "lastfm_stats"),
                (enrich_deezer_artist, "deezer_artist_stats"),
                (enrich_similar_artists, "similar_artists"),
                (enrich_musicbrainz_rich, "musicbrainz_rich"),
            ]

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="stage3"
            ) as pool:
                futures = {
                    pool.submit(
                        fn,
                        batch_size=batch_size,
                        stop_event=stop_event,
                        on_progress=self._progress_fn(on_progress, key),
                    ): key
                    for fn, key in stage3_workers
                }
                for future in concurrent.futures.as_completed(futures):
                    key = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error("PipelineRunner: Stage 3 '%s' failed: %s", key, e)

            # === Pipeline complete ===
            if self._stopped(stop_event):
                result["status"] = "stopped"
            else:
                rythmx_store.set_setting("library_last_synced", datetime.utcnow().isoformat())
                logger.info("PipelineRunner: pipeline complete")

        except Exception as e:
            logger.exception("PipelineRunner: unhandled error: %s", e)
            result["status"] = "error"
            result["error"] = str(e)

        finally:
            heartbeat_cancel.set()

        return result
