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

# Worker key constants - canonical source of truth for pipeline progress/status.
WORKER_KEYS = {
    # Identity - artist
    "itunes_artist": "itunes_artist",
    "deezer_artist": "deezer_artist",
    "spotify_artist": "spotify_artist",
    "lastfm_artist": "lastfm_artist",
    # Identity - album
    "itunes_album": "itunes_album",
    "deezer_album": "deezer_album",
    # Artwork
    "artist_art": "artist_art",
    "album_art_local": "album_art_local",
    "album_art_cdn": "album_art_cdn",
    "album_art_prewarm": "album_art_prewarm",
    # Rich metadata
    "itunes_rich": "itunes_rich",
    "deezer_rich": "deezer_rich",
    "spotify_genres": "spotify_genres",
    "lastfm_tags": "lastfm_tags",
    "lastfm_stats": "lastfm_stats",
    "deezer_artist_stats": "deezer_artist_stats",
    "similar_artists": "similar_artists",
    "musicbrainz_rich": "musicbrainz_rich",
    "musicbrainz_album_rich": "musicbrainz_album_rich",
}

LEGACY_TO_CANONICAL_WORKER_KEYS = {
    "itunes": WORKER_KEYS["itunes_album"],
    "deezer": WORKER_KEYS["deezer_album"],
    "spotify_id": WORKER_KEYS["spotify_artist"],
    "lastfm_id": WORKER_KEYS["lastfm_artist"],
}

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
        on_substep: "callable | None" = None,
    ) -> dict:
        """Execute the full enrichment DAG. Returns summary dict."""
        if not self._lock.acquire(blocking=False):
            logger.info("PipelineRunner: already running — skipping")
            return {"status": "skipped", "reason": "already_running"}

        try:
            return self._execute(batch_size, stop_event, on_progress, on_phase, on_substep)
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

    @staticmethod
    def _fanout_progress_fn(on_progress, keys: tuple[str, ...]):
        """Broadcast one worker's aggregate progress to multiple UI worker keys."""
        if on_progress is None:
            return None
        callbacks = [on_progress(k) for k in keys]

        def _fn(found: int, not_found: int, errors: int, total: int) -> None:
            for cb in callbacks:
                cb(found, not_found, errors, total)

        return _fn

    @staticmethod
    def _emit_substep(on_substep, substep: str, status: str) -> None:
        if on_substep is None:
            return
        try:
            on_substep(substep, status)
        except Exception:
            pass

    @staticmethod
    def read_worker_snapshot() -> dict[str, dict[str, int]]:
        """
        Read worker counters from enrichment_meta and normalize legacy keys to
        canonical worker names used by the UI contract.
        """
        from app.db.rythmx_store import _connect

        workers: dict[str, dict[str, int]] = {}
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT source, status, COUNT(*) AS cnt
                FROM enrichment_meta
                GROUP BY source, status
                """
            ).fetchall()

        for row in rows:
            source = str(row["source"])
            key = LEGACY_TO_CANONICAL_WORKER_KEYS.get(source, source)
            if key not in workers:
                workers[key] = {"found": 0, "not_found": 0, "errors": 0, "pending": 0}
            field = "errors" if row["status"] == "error" else row["status"]
            if field in workers[key]:
                workers[key][field] += int(row["cnt"] or 0)

        return workers

    @classmethod
    def _persist_last_run(cls, started_at: str | None, outcome: str) -> None:
        """Persist pipeline run summary into app_settings for REST/UI consumers."""
        ended_at = datetime.utcnow().isoformat()
        started = started_at or ended_at
        try:
            duration_s = max(
                0,
                int((datetime.fromisoformat(ended_at) - datetime.fromisoformat(started)).total_seconds()),
            )
        except Exception:
            duration_s = 0

        enriched = 0
        not_found = 0
        try:
            workers = cls.read_worker_snapshot()
            enriched = sum(int(v.get("found", 0)) for v in workers.values())
            not_found = sum(int(v.get("not_found", 0)) for v in workers.values())
        except Exception:
            pass

        rythmx_store.set_setting("last_run_started_at", started)
        rythmx_store.set_setting("last_run_ended_at", ended_at)
        rythmx_store.set_setting("last_run_duration_s", str(duration_s))
        rythmx_store.set_setting("last_run_outcome", outcome)
        rythmx_store.set_setting("last_run_enriched", str(enriched))
        rythmx_store.set_setting("last_run_not_found", str(not_found))

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
        on_substep: "callable | None" = None,
    ) -> dict:
        # Prune stale lock from a crashed previous run
        if self._is_stale_lock():
            logger.warning("PipelineRunner: pruning stale lock (previous run crashed)")
            self._clear_state()

        # Mark start
        run_started_at = datetime.utcnow().isoformat()
        rythmx_store.set_setting(_KEY_STARTED, run_started_at)
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
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Stage 1.1: Tag Enrichment (bitrate/codec/container from local files) ===
            # Navidrome-only; skipped automatically when MUSIC_DIR is not configured.
            self._set_phase("tag_enrichment", on_phase)
            try:
                from app.config import MUSIC_DIR
                if MUSIC_DIR:
                    from app.services.enrichment.tag_enrichment import enrich_tags
                    tag_result = enrich_tags(
                        batch_size=50,
                        stop_event=stop_event,
                        on_progress=self._progress_fn(on_progress, "tag_enrichment"),
                    )
                    result["tag_enrichment"] = tag_result
                    logger.info(
                        "PipelineRunner: tag_enrichment — processed=%d skipped=%d errors=%d",
                        tag_result.get("processed", 0),
                        tag_result.get("skipped", 0),
                        tag_result.get("errors", 0),
                    )
                else:
                    logger.info("PipelineRunner: tag_enrichment skipped (MUSIC_DIR not set)")
            except Exception as e:
                logger.warning("PipelineRunner: tag_enrichment failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Stage 1.15: Repair stale artwork hashes (missing originals) ===
            self._set_phase("artwork_repair", on_phase)
            try:
                from app.services.enrichment.artwork_repair import reset_missing_content_hashes
                repair_result = reset_missing_content_hashes(entity_types=("album", "artist"))
                result["artwork_repair"] = repair_result
                logger.info(
                    "PipelineRunner: artwork_repair - scanned=%d reset=%d",
                    repair_result.get("scanned", 0),
                    repair_result.get("reset", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: artwork_repair failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Stage 1.2a: Album Artwork — Local File Pass ===
            # Zero network — embedded APIC/PICTURE and sidecar files only.
            # Runs before Stage 2a so artwork is available as early as possible.
            self._set_phase("album_art_local", on_phase)
            try:
                from app.services.enrichment.art_album import enrich_album_art_local
                local_art_result = enrich_album_art_local(
                    batch_size=2000,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, WORKER_KEYS["album_art_local"]),
                )
                result["album_art_local"] = local_art_result
                logger.info(
                    "PipelineRunner: album_art_local - enriched=%d skipped=%d remaining=%d",
                    local_art_result.get("enriched", 0),
                    local_art_result.get("skipped", 0),
                    local_art_result.get("remaining", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: album_art_local failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Stage 2a: iTunes/Deezer IDs (sequential — feeds catalog promotion) ===
            self._set_phase("id_itunes_deezer", on_phase)
            modified_artist_ids: list[str] = []
            try:
                from app.services.enrichment.id_itunes_deezer import enrich_library
                s2a_result = enrich_library(
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._fanout_progress_fn(
                        on_progress,
                        (WORKER_KEYS["itunes_album"], WORKER_KEYS["deezer_album"]),
                    ),
                )
                modified_artist_ids = s2a_result.get("modified_artist_ids", [])
                logger.info(
                    "PipelineRunner: id_itunes_deezer - enriched=%d skipped=%d failed=%d remaining=%d",
                    s2a_result.get("enriched", 0),
                    s2a_result.get("skipped", 0),
                    s2a_result.get("failed", 0),
                    s2a_result.get("remaining", 0),
                )
            except Exception as e:
                logger.error("PipelineRunner: iTunes/Deezer IDs failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Stage 1.2b: Album Artwork — CDN Pass ===
            # Runs after Stage 2a so deezer_id / itunes_album_id are populated.
            # Skips albums with no metadata match. 30-day gate via enrichment_meta.
            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            self._set_phase("album_art_cdn", on_phase)
            try:
                from app.services.enrichment.art_album import enrich_album_art_cdn
                cdn_art_result = enrich_album_art_cdn(
                    batch_size=200,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, WORKER_KEYS["album_art_cdn"]),
                )
                result["album_art_cdn"] = cdn_art_result
                logger.info(
                    "PipelineRunner: album_art_cdn - enriched=%d skipped=%d remaining=%d",
                    cdn_art_result.get("enriched", 0),
                    cdn_art_result.get("skipped", 0),
                    cdn_art_result.get("remaining", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: album_art_cdn failed: %s", e)

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
                    on_progress=self._progress_fn(on_progress, WORKER_KEYS["spotify_artist"]),
                )
                fut_lastfm = pool.submit(
                    enrich_artist_ids_lastfm,
                    batch_size=batch_size,
                    stop_event=stop_event,
                    on_progress=self._progress_fn(on_progress, WORKER_KEYS["lastfm_artist"]),
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
                    on_progress=self._progress_fn(on_progress, WORKER_KEYS["artist_art"]),
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
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Ownership Chain (sequential — each reads previous output) ===

            # Ownership sync
            self._set_phase("ownership_sync", on_phase)
            self._emit_substep(on_substep, "ownership_sync", "running")
            try:
                from app.services.enrichment.ownership_sync import sync_release_ownership
                own_result = sync_release_ownership()
                result["ownership_sync"] = own_result
                logger.info(
                    "PipelineRunner: ownership sync — id=%d title=%d",
                    own_result.get("owned_by_id", 0),
                    own_result.get("owned_by_title", 0),
                )
                self._emit_substep(on_substep, "ownership_sync", "completed")
            except Exception as e:
                logger.warning("PipelineRunner: ownership sync failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # Normalize titles (scoped to modified artists when available)
            self._set_phase("normalize_titles", on_phase)
            self._emit_substep(on_substep, "normalize_titles", "running")
            scope = modified_artist_ids or None
            try:
                from app.db.rythmx_store import recompute_normalized_titles
                recomputed = recompute_normalized_titles(artist_ids=scope)
                result["recompute_titles"] = recomputed
                logger.info("PipelineRunner: normalized_title recomputed for %d rows", recomputed)
                self._emit_substep(on_substep, "normalize_titles", "completed")
            except Exception as e:
                logger.warning("PipelineRunner: normalized_title recompute failed: %s", e)

            # Missing counts (scoped to modified artists when available)
            self._set_phase("missing_counts", on_phase)
            self._emit_substep(on_substep, "missing_counts", "running")
            try:
                from app.db.rythmx_store import refresh_missing_counts
                missing_updated = refresh_missing_counts(artist_ids=scope)
                logger.info("PipelineRunner: missing_count refresh - %d artists updated", missing_updated)
                self._emit_substep(on_substep, "missing_counts", "completed")
            except Exception as e:
                logger.warning("PipelineRunner: missing_count refresh failed: %s", e)

            # Canonical grouping (scoped to modified artists when available)
            self._set_phase("canonical", on_phase)
            self._emit_substep(on_substep, "canonical", "running")
            try:
                from app.db.rythmx_store import populate_canonical_release_ids
                canonical_updated = populate_canonical_release_ids(artist_ids=scope)
                result["canonical_refresh"] = canonical_updated
                self._emit_substep(on_substep, "canonical", "completed")
                logger.info("PipelineRunner: canonical refresh — %d rows", canonical_updated)
            except Exception as e:
                logger.warning("PipelineRunner: canonical refresh failed: %s", e)

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
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
            from app.services.enrichment.rich_musicbrainz_album import enrich_musicbrainz_album_rich

            stage3_workers = [
                (enrich_itunes_rich, WORKER_KEYS["itunes_rich"]),
                (enrich_deezer_release, WORKER_KEYS["deezer_rich"]),
                (enrich_genres_spotify, WORKER_KEYS["spotify_genres"]),
                (enrich_tags_lastfm, WORKER_KEYS["lastfm_tags"]),
                (enrich_stats_lastfm, WORKER_KEYS["lastfm_stats"]),
                (enrich_deezer_artist, WORKER_KEYS["deezer_artist_stats"]),
                (enrich_similar_artists, WORKER_KEYS["similar_artists"]),
                (enrich_musicbrainz_rich, WORKER_KEYS["musicbrainz_rich"]),
                (enrich_musicbrainz_album_rich, WORKER_KEYS["musicbrainz_album_rich"]),
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

            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
                return result

            # === Post-Stage-3: low-priority album artwork pre-warmer ===
            self._set_phase("album_art_prewarm", on_phase)
            try:
                from app.services.enrichment.art_album import prewarm_album_art_cache
                prewarm_result = prewarm_album_art_cache(size=300, limit=2000)
                result["album_art_prewarm"] = prewarm_result
                logger.info(
                    "PipelineRunner: album_art_prewarm - warmed=%d errors=%d candidates=%d",
                    prewarm_result.get("warmed", 0),
                    prewarm_result.get("errors", 0),
                    prewarm_result.get("candidates", 0),
                )
            except Exception as e:
                logger.warning("PipelineRunner: album_art_prewarm failed: %s", e)

            # === Pipeline complete ===
            if self._stopped(stop_event):
                result["status"] = "stopped"
                self._persist_last_run(run_started_at, "stopped")
            else:
                rythmx_store.set_setting("library_last_synced", datetime.utcnow().isoformat())
                self._persist_last_run(run_started_at, "completed")
                logger.info("PipelineRunner: pipeline complete")

        except Exception as e:
            logger.exception("PipelineRunner: unhandled error: %s", e)
            result["status"] = "error"
            result["error"] = str(e)
            self._persist_last_run(run_started_at, "error")

        finally:
            heartbeat_cancel.set()

        return result
