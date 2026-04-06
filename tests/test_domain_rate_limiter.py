"""
test_domain_rate_limiter.py — Unit tests for TokenBucket and DomainRateLimiter.

All tests mock time.sleep and time.monotonic to avoid real waits.
Focus: public contracts (not threading internals).
"""
import time
from unittest.mock import patch

import pytest

from app.services.api_orchestrator import (
    TokenBucket,
    DomainRateLimiter,
    _CIRCUIT_BREAKER_THRESHOLD,
    _CIRCUIT_BREAKER_PAUSE_S,
)

_MOD = "app.services.api_orchestrator"


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------

class TestTokenBucket:
    def test_fresh_bucket_permits_capacity_acquires_without_sleeping(self):
        """A fresh bucket with capacity 3 allows 3 acquires without sleeping."""
        bucket = TokenBucket(rate=100.0, capacity=3)
        with patch(f"{_MOD}.time.sleep") as mock_sleep:
            for _ in range(3):
                bucket.acquire()
        mock_sleep.assert_not_called()

    def test_empty_bucket_calls_sleep(self):
        """Acquiring from an empty bucket calls time.sleep at least once."""
        bucket = TokenBucket(rate=1.0, capacity=1)
        bucket._tokens = 0.0

        base = time.monotonic()
        call_count = [0]

        def advancing_monotonic():
            call_count[0] += 1
            # After the first sleep iteration advance time so tokens refill
            return base if call_count[0] < 4 else base + 2.0

        with patch(f"{_MOD}.time.monotonic", side_effect=advancing_monotonic):
            with patch(f"{_MOD}.time.sleep") as mock_sleep:
                bucket.acquire()

        mock_sleep.assert_called()

    def test_record_429_increments_consecutive_counter(self):
        """record_429() increments _consecutive_429s by 1."""
        bucket = TokenBucket(rate=100.0, capacity=5)
        with patch(f"{_MOD}.time.sleep"):
            bucket.record_429("itunes")
        assert bucket._consecutive_429s == 1

    def test_circuit_breaker_trips_after_threshold(self):
        """Circuit breaker opens after _CIRCUIT_BREAKER_THRESHOLD consecutive 429s."""
        bucket = TokenBucket(rate=100.0, capacity=5)
        with patch(f"{_MOD}.time.sleep"):
            for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
                bucket.record_429("itunes")
        assert bucket._circuit_open_until > time.monotonic()

    def test_circuit_breaker_pauses_for_configured_duration(self):
        """circuit_open_until is set at least _CIRCUIT_BREAKER_PAUSE_S seconds ahead."""
        bucket = TokenBucket(rate=100.0, capacity=5)
        before = time.monotonic()
        with patch(f"{_MOD}.time.sleep"):
            for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
                bucket.record_429("itunes")
        assert bucket._circuit_open_until >= before + _CIRCUIT_BREAKER_PAUSE_S

    def test_record_success_resets_consecutive_counter(self):
        """record_success() clears _consecutive_429s back to 0."""
        bucket = TokenBucket(rate=100.0, capacity=5)
        with patch(f"{_MOD}.time.sleep"):
            bucket.record_429("itunes")
            bucket.record_429("itunes")
        bucket.record_success("itunes")
        assert bucket._consecutive_429s == 0

    def test_record_success_after_two_429s_does_not_clear_circuit_open(self):
        """
        Two 429s below threshold: record_success resets counter.
        circuit_open_until is 0.0 (never tripped).
        """
        bucket = TokenBucket(rate=100.0, capacity=5)
        with patch(f"{_MOD}.time.sleep"):
            for _ in range(_CIRCUIT_BREAKER_THRESHOLD - 1):
                bucket.record_429("itunes")
        bucket.record_success("itunes")
        assert bucket._consecutive_429s == 0
        assert bucket._circuit_open_until == 0.0


# ---------------------------------------------------------------------------
# DomainRateLimiter
# ---------------------------------------------------------------------------

class TestDomainRateLimiter:
    def test_acquire_known_domain_does_not_raise(self):
        """acquire() on a known domain completes without raising."""
        limiter = DomainRateLimiter()
        # itunes has capacity=3 — first call is instant, no sleep needed
        limiter.acquire("itunes")

    def test_acquire_all_known_domains_without_error(self):
        """All configured domains can be acquired at least once instantly."""
        limiter = DomainRateLimiter()
        for domain in ("itunes", "deezer", "lastfm", "spotify", "fanart", "musicbrainz"):
            limiter.acquire(domain)   # each has capacity ≥ 2, first call instant

    def test_unknown_domain_does_not_raise(self):
        """acquire() on an unknown domain returns without blocking or raising."""
        limiter = DomainRateLimiter()
        limiter.acquire("totally-unknown-domain")

    def test_record_429_unknown_domain_is_noop(self):
        """record_429 on an unknown domain does not raise."""
        limiter = DomainRateLimiter()
        limiter.record_429("no-such-domain")

    def test_record_success_unknown_domain_is_noop(self):
        """record_success on an unknown domain does not raise."""
        limiter = DomainRateLimiter()
        limiter.record_success("no-such-domain")

    def test_circuit_breaker_via_limiter(self):
        """DomainRateLimiter.record_429 forwards to the correct TokenBucket."""
        limiter = DomainRateLimiter()
        with patch(f"{_MOD}.time.sleep"):
            for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
                limiter.record_429("itunes")
        bucket = limiter._buckets["itunes"]
        assert bucket._circuit_open_until > time.monotonic()

    def test_record_success_via_limiter_resets_bucket(self):
        """DomainRateLimiter.record_success resets the correct bucket's counter."""
        limiter = DomainRateLimiter()
        with patch(f"{_MOD}.time.sleep"):
            limiter.record_429("deezer")
        limiter.record_success("deezer")
        assert limiter._buckets["deezer"]._consecutive_429s == 0
