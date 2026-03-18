"""
api_orchestrator.py — Shared API rate limiting for all external service calls.

Two-layer design:
  Layer 1 — DomainRateLimiter (this module):
      Token-bucket rate limiting per external domain.
      All clients call rate_limiter.acquire(domain) before each request.
      Thread-safe via per-domain Lock (no GIL reliance).

  Layer 2 — EnrichmentOrchestrator (future, same file):
      DAG runner that sequences enrichment phases:
        Phase 1 (sequential) → Phase 2 (parallel) → Phase 3 (dependent on Phase 1)
      Delegates actual API calls to existing library_service.py functions.
      No API logic lives here — conductor only.

Domain limits (requests per minute):
  itunes:        20/min  (Apple free tier — enforced; no auth required)
  deezer:        50/min  (Deezer documented free limit)
  lastfm:       200/min  (generous free tier)
  spotify:      100/min  (varies by endpoint; conservative baseline)
  fanart:       120/min  (2/sec — within limits for personal projects)
  musicbrainz:    1/min  (strict: 1/sec; resets aggressively in Docker)

Usage in any client:
    from app.services.api_orchestrator import rate_limiter
    rate_limiter.acquire("itunes")        # blocks until token available
    resp = session.get(url, ...)
    if resp.status_code == 429:
        rate_limiter.record_429("itunes")
        return None
    rate_limiter.record_success("itunes")

To add a new source (e.g. SoundCloud):
    1. Add entry to _DOMAIN_CONFIGS below with rate + capacity.
    2. Call rate_limiter.acquire("soundcloud") in your client before each request.
    3. Handle 429: rate_limiter.record_429("soundcloud") then return None.
    4. Determine DAG phase for EnrichmentOrchestrator (see docs/api-dependency-map.md).
    5. No other changes needed unless it creates a new phase dependency.
"""
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain configuration
# Add new external services here. rate = req/sec (not req/min).
# capacity = max burst tokens (allows short bursts, then throttles to rate).
# ---------------------------------------------------------------------------

_DOMAIN_CONFIGS: dict[str, dict] = {
    "itunes":       {"rate": 20 / 60,   "capacity": 3},
    "deezer":       {"rate": 50 / 60,   "capacity": 5},
    "lastfm":       {"rate": 200 / 60,  "capacity": 10},
    "spotify":      {"rate": 100 / 60,  "capacity": 5},
    "fanart":       {"rate": 120 / 60,  "capacity": 5},
    "musicbrainz":  {"rate": 1 / 60,    "capacity": 1},
}

_CIRCUIT_BREAKER_THRESHOLD = 3    # consecutive 429s before tripping
_CIRCUIT_BREAKER_PAUSE_S   = 60   # seconds to pause domain when circuit trips


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------

class TokenBucket:
    """
    Token bucket for a single API domain.

    Tokens refill at `rate` tokens/second up to `capacity`.
    acquire() blocks the calling thread (releases the lock while sleeping)
    until a token is available.

    record_429() applies exponential backoff + jitter; trips the circuit
    breaker after _CIRCUIT_BREAKER_THRESHOLD consecutive 429s.
    record_success() resets the consecutive-429 counter.
    """

    def __init__(self, rate: float, capacity: int):
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._consecutive_429s = 0
        self._circuit_open_until: float = 0.0

    def acquire(self) -> None:
        """Block until a token is available. Respects circuit breaker state."""
        while True:
            with self._lock:
                now = time.monotonic()
                if now < self._circuit_open_until:
                    wait = self._circuit_open_until - now
                else:
                    elapsed = now - self._last_refill
                    self._tokens = min(
                        self._capacity, self._tokens + elapsed * self._rate
                    )
                    self._last_refill = now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) / self._rate
            # Sleep outside the lock so other threads can proceed
            time.sleep(wait)

    def record_429(self, domain: str) -> None:
        """
        Record a 429 response. Applies jitter backoff; trips circuit breaker
        after _CIRCUIT_BREAKER_THRESHOLD consecutive 429s.
        """
        delay = 0.0
        with self._lock:
            self._consecutive_429s += 1
            if self._consecutive_429s >= _CIRCUIT_BREAKER_THRESHOLD:
                self._circuit_open_until = (
                    time.monotonic() + _CIRCUIT_BREAKER_PAUSE_S
                )
                logger.warning(
                    "api_orchestrator: circuit breaker OPEN for '%s' — "
                    "%d consecutive 429s; pausing %ds",
                    domain, self._consecutive_429s, _CIRCUIT_BREAKER_PAUSE_S,
                )
            else:
                base = 2.0 ** self._consecutive_429s
                delay = base * (1 + random.uniform(0, 0.5))
                logger.warning(
                    "api_orchestrator: 429 from '%s' (hit #%d) — backoff %.1fs",
                    domain, self._consecutive_429s, delay,
                )
        if delay:
            time.sleep(delay)

    def record_success(self, domain: str) -> None:  # noqa: ARG002
        """Reset consecutive-429 counter on a successful response."""
        with self._lock:
            self._consecutive_429s = 0


# ---------------------------------------------------------------------------
# DomainRateLimiter — shared singleton
# ---------------------------------------------------------------------------

class DomainRateLimiter:
    """
    Shared rate limiter across all API clients.

    Instantiated once as `rate_limiter` at module level. All clients import
    this singleton — guarantees one token bucket per domain regardless of
    how many client modules are active simultaneously.

    Unknown domains log a warning and proceed without limiting, so new
    providers don't fail silently — they'll just appear in the warning log.
    """

    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {
            domain: TokenBucket(cfg["rate"], cfg["capacity"])
            for domain, cfg in _DOMAIN_CONFIGS.items()
        }

    def acquire(self, domain: str) -> None:
        """
        Block until a rate-limit token is available for `domain`.
        Call this before every external HTTP request.
        """
        bucket = self._buckets.get(domain)
        if bucket is None:
            logger.warning(
                "api_orchestrator: unknown domain '%s' — no rate limit applied. "
                "Add it to _DOMAIN_CONFIGS.",
                domain,
            )
            return
        bucket.acquire()

    def record_429(self, domain: str) -> None:
        """Call when the API returns HTTP 429 for `domain`."""
        bucket = self._buckets.get(domain)
        if bucket:
            bucket.record_429(domain)

    def record_success(self, domain: str) -> None:
        """Call after a successful (non-429) response to reset backoff state."""
        bucket = self._buckets.get(domain)
        if bucket:
            bucket.record_success(domain)


# Singleton — import this, not the class directly.
rate_limiter = DomainRateLimiter()


# ---------------------------------------------------------------------------
# EnrichmentOrchestrator — DAG conductor for library enrichment pipeline
# ---------------------------------------------------------------------------

class EnrichmentOrchestrator:
    """
    Conductor for the 4-stage library enrichment pipeline.

    Does NOT make API calls. Sequences existing library_service.py workers:
      Stage 2 (sequential): enrich_library → enrich_artist_ids_spotify → enrich_artist_ids_lastfm
      Stage 3 (parallel):   enrich_itunes_rich, enrich_deezer_release, enrich_genres_spotify,
                             enrich_tags_lastfm, enrich_stats_lastfm
      BPM (last):           enrich_deezer_bpm (depends on deezer_id from Stage 2)

    Broadcasts SHRTA enrichment_progress events after each worker via ws.broadcast().
    Broadcasts enrichment_complete or enrichment_stopped on finish.

    Usage:
        EnrichmentOrchestrator.get().run_full()   # fire-and-forget
        EnrichmentOrchestrator.get().stop()        # graceful stop after current batch
        EnrichmentOrchestrator.get().is_running()  # True while thread alive
    """

    _instance: "EnrichmentOrchestrator | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def get(cls) -> "EnrichmentOrchestrator":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def run_full(self, batch_size: int = 50) -> None:
        """Start full pipeline in background thread. No-op if already running."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(batch_size,),
                daemon=True,
                name="enrichment-orchestrator",
            )
            self._thread.start()

    def stop(self) -> None:
        """Signal all workers to stop after their current batch."""
        self._stop_event.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _broadcast_worker(self, worker: str, running: bool = True) -> None:
        """Query enrichment_meta for current counts and broadcast progress."""
        try:
            from app.routes.ws import broadcast
            from app.db.rythmx_store import _connect
            with _connect() as conn:
                rows = conn.execute(
                    """
                    SELECT status, COUNT(*) as cnt
                    FROM enrichment_meta
                    WHERE source = ?
                    GROUP BY status
                    """,
                    (worker,),
                ).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            broadcast("enrichment_progress", {
                "worker": worker,
                "found": counts.get("found", 0),
                "not_found": counts.get("not_found", 0),
                "errors": counts.get("error", 0),
                "pending": counts.get("pending", 0),
                "running": running,
            })
        except Exception as e:
            logger.warning("EnrichmentOrchestrator: broadcast failed for '%s': %s", worker, e)

    def _broadcast_complete(self) -> None:
        try:
            from app.routes.ws import broadcast
            from app.db.rythmx_store import _connect
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT source, status, COUNT(*) as cnt FROM enrichment_meta GROUP BY source, status"
                ).fetchall()
            workers: dict = {}
            for r in rows:
                src = r["source"]
                if src not in workers:
                    workers[src] = {"found": 0, "not_found": 0, "errors": 0, "pending": 0, "running": False}
                field = "errors" if r["status"] == "error" else r["status"]
                if field in workers[src]:
                    workers[src][field] = r["cnt"]
            broadcast("enrichment_complete", {"workers": workers})
        except Exception as e:
            logger.warning("EnrichmentOrchestrator: broadcast_complete failed: %s", e)

    def _run(self, batch_size: int) -> None:
        from app.services import library_service as ls
        logger.info("EnrichmentOrchestrator: pipeline start (batch_size=%d)", batch_size)

        try:
            # --- Stage 2: sequential (IDs must be resolved before Stage 3) ---
            for worker_fn, worker_key in [
                (lambda: ls.enrich_library(batch_size, self._stop_event), "itunes"),
                (lambda: ls.enrich_artist_ids_spotify(batch_size, self._stop_event), "spotify_id"),
                (lambda: ls.enrich_artist_ids_lastfm(batch_size, self._stop_event), "lastfm_id"),
            ]:
                if self._stop_event.is_set():
                    break
                try:
                    worker_fn()
                except Exception as e:
                    logger.error("EnrichmentOrchestrator: Stage 2 worker error: %s", e)
                self._broadcast_worker(worker_key)

            if self._stop_event.is_set():
                try:
                    from app.routes.ws import broadcast
                    broadcast("enrichment_stopped", {"message": "Enrichment stopped by user"})
                except Exception:
                    pass
                logger.info("EnrichmentOrchestrator: stopped after Stage 2")
                return

            # --- Stage 3: parallel (independent rich-data workers) ---
            stage3_workers = [
                (ls.enrich_itunes_rich, batch_size, "itunes_rich"),
                (ls.enrich_deezer_release, batch_size, "deezer_rich"),
                (ls.enrich_genres_spotify, batch_size, "spotify_genres"),
                (ls.enrich_tags_lastfm, batch_size, "lastfm_tags"),
                (ls.enrich_stats_lastfm, batch_size, "lastfm_stats"),
            ]

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(fn, bs, self._stop_event): key
                    for fn, bs, key in stage3_workers
                }
                for future in futures:
                    key = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error("EnrichmentOrchestrator: Stage 3 '%s' error: %s", key, e)
                    self._broadcast_worker(key)

            # --- BPM: last (depends on deezer_id from Stage 2) ---
            if not self._stop_event.is_set():
                try:
                    ls.enrich_deezer_bpm(30, self._stop_event)
                except Exception as e:
                    logger.error("EnrichmentOrchestrator: BPM worker error: %s", e)
                self._broadcast_worker("deezer_bpm")

            if self._stop_event.is_set():
                try:
                    from app.routes.ws import broadcast
                    broadcast("enrichment_stopped", {"message": "Enrichment stopped by user"})
                except Exception:
                    pass
                logger.info("EnrichmentOrchestrator: stopped during Stage 3 / BPM")
            else:
                self._broadcast_complete()
                logger.info("EnrichmentOrchestrator: pipeline complete")

        except Exception as e:
            logger.exception("EnrichmentOrchestrator: unhandled error: %s", e)
