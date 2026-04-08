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
  deezer:       300/min  (Deezer allows 50 req/5s; 300/min is conservative)
  lastfm:       200/min  (generous free tier)
  spotify:      100/min  (varies by endpoint; conservative baseline)
  fanart:       120/min  (2/sec — within limits for personal projects)
  musicbrainz:   50/min  (MB allows 1/sec = 60/min; 50/min is conservative)

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
from datetime import datetime, timezone

from app.db import rythmx_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain configuration
# Add new external services here. rate = req/sec (not req/min).
# capacity = max burst tokens (allows short bursts, then throttles to rate).
# ---------------------------------------------------------------------------

_DOMAIN_CONFIGS: dict[str, dict] = {
    "itunes":       {"rate": 20 / 60,   "capacity": 3},
    "deezer":       {"rate": 300 / 60,  "capacity": 10},   # Deezer allows 50 req/5s; 300/min is conservative
    "lastfm":       {"rate": 200 / 60,  "capacity": 10},
    "spotify":      {"rate": 100 / 60,  "capacity": 5},
    "fanart":       {"rate": 120 / 60,  "capacity": 5},
    "musicbrainz":  {"rate": 50 / 60,   "capacity": 2},    # MB allows 1 req/s (60/min); 50/min is conservative
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
# EnrichmentOrchestrator — UI-facing thin wrapper for PipelineRunner
# ---------------------------------------------------------------------------

class EnrichmentOrchestrator:
    """
    UI-facing singleton that manages the enrichment pipeline thread.

    Delegates all stage execution to PipelineRunner (app.services.enrichment.runner).
    This class owns: thread lifecycle, WS progress broadcasting, stop signal.
    PipelineRunner owns: stage DAG, parallelism, heartbeat, lock.

    Usage:
        EnrichmentOrchestrator.get().run_full()   # fire-and-forget
        EnrichmentOrchestrator.get().stop()        # graceful stop after current batch
        EnrichmentOrchestrator.get().is_running()  # True while thread alive
    """

    _instance: "EnrichmentOrchestrator | None" = None
    _instance_lock = threading.Lock()
    _DEFAULT_SUBSTEPS = {
        "ownership_sync": "pending",
        "normalize_titles": "pending",
        "missing_counts": "pending",
        "canonical": "pending",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: str | None = None
        self.current_substeps = dict(self._DEFAULT_SUBSTEPS)

    @classmethod
    def get(cls) -> "EnrichmentOrchestrator":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def run_full(self, batch_size: int = 10_000) -> None:
        """Start full pipeline in background thread. No-op if already running.
        batch_size is effectively unlimited — rate limiter + stop_event are the real throttle."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._started_at = datetime.now(timezone.utc).isoformat()
            self.current_substeps = dict(self._DEFAULT_SUBSTEPS)
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

    def _make_progress_fn(self, worker_key: str) -> "callable":
        """Return a per-item progress callback that broadcasts enrichment_progress via WS."""
        def _fn(found: int, not_found: int, errors: int, total: int) -> None:
            try:
                from app.routes.ws import broadcast
                broadcast("enrichment_progress", {
                    "worker": worker_key,
                    "found": found,
                    "not_found": not_found,
                    "errors": errors,
                    "pending": max(0, total - found - not_found - errors),
                    "running": True,
                })
            except Exception:
                pass
        return _fn

    @staticmethod
    def _make_phase_fn():
        """Return a callback that broadcasts pipeline phase transitions via WS."""
        def _fn(phase: str) -> None:
            try:
                from app.routes.ws import broadcast
                broadcast("enrichment_phase", {"phase": phase})
            except Exception:
                pass
        return _fn

    def _make_substep_fn(self):
        """Return a callback that updates in-memory substeps and broadcasts via WS."""
        def _fn(substep: str, status: str) -> None:
            self.current_substeps[substep] = status
            try:
                from app.routes.ws import broadcast
                broadcast("enrichment_substep", {"substep": substep, "status": status})
            except Exception:
                pass
        return _fn

    @staticmethod
    def _read_last_run() -> dict | None:
        started_at = rythmx_store.get_setting("last_run_started_at")
        ended_at = rythmx_store.get_setting("last_run_ended_at")
        outcome = rythmx_store.get_setting("last_run_outcome")
        if not started_at or not ended_at or not outcome:
            return None
        duration_raw = rythmx_store.get_setting("last_run_duration_s", "0") or "0"
        enriched_raw = rythmx_store.get_setting("last_run_enriched", "0") or "0"
        not_found_raw = rythmx_store.get_setting("last_run_not_found", "0") or "0"
        try:
            duration_s = int(duration_raw)
        except (TypeError, ValueError):
            duration_s = 0
        try:
            enriched = int(enriched_raw)
        except (TypeError, ValueError):
            enriched = 0
        try:
            not_found = int(not_found_raw)
        except (TypeError, ValueError):
            not_found = 0
        return {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": duration_s,
            "outcome": outcome,
            "enriched": enriched,
            "not_found": not_found,
        }

    def _broadcast_complete(self) -> None:
        try:
            from app.routes.ws import broadcast
            from app.services.enrichment.runner import PipelineRunner

            workers = PipelineRunner.read_worker_snapshot()
            broadcast("enrichment_complete", {
                "workers": workers,
                "last_run": self._read_last_run(),
            })
        except Exception as e:
            logger.warning("EnrichmentOrchestrator: broadcast_complete failed: %s", e)

    def _run(self, batch_size: int) -> None:
        """Delegate to PipelineRunner — single control plane for all stages."""
        from app.services.enrichment.runner import PipelineRunner
        logger.info("EnrichmentOrchestrator: pipeline start (batch_size=%d)", batch_size)

        try:
            runner = PipelineRunner()
            result = runner.run(
                batch_size=batch_size,
                stop_event=self._stop_event,
                on_progress=self._make_progress_fn,
                on_phase=self._make_phase_fn(),
                on_substep=self._make_substep_fn(),
            )

            if result.get("status") == "stopped":
                self._started_at = None
                try:
                    from app.routes.ws import broadcast
                    broadcast("enrichment_stopped", {
                        "message": "Enrichment stopped by user",
                        "last_run": self._read_last_run(),
                    })
                except Exception:
                    pass
                logger.info("EnrichmentOrchestrator: pipeline stopped by user")
            else:
                self._started_at = None
                self._broadcast_complete()
                logger.info("EnrichmentOrchestrator: pipeline complete")

        except Exception as e:
            logger.exception("EnrichmentOrchestrator: unhandled error: %s", e)
