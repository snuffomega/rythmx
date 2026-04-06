"""
test_image_service.py — Unit tests for image_service.py.

Tests pure functions and cache-tier behaviour.
All external HTTP calls and DB access are mocked — no real network or DB needed.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from app.services.image_service import (
    _norm_text,
    _norm_album_title,
    _similarity,
    _select_itunes_album_art,
    _mem_cache,
    _mem_cache_put,
    _MEM_MAX,
    resolve_image,
)


# ---------------------------------------------------------------------------
# _norm_text
# ---------------------------------------------------------------------------

class TestNormText:
    def test_strips_punctuation_and_lowercases(self):
        assert _norm_text("Foo Bar!") == "foo bar"

    def test_collapses_internal_whitespace(self):
        assert _norm_text("hello   world") == "hello world"

    def test_trims_leading_trailing_whitespace(self):
        assert _norm_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert _norm_text("") == ""

    def test_none_value(self):
        assert _norm_text(None) == ""


# ---------------------------------------------------------------------------
# _norm_album_title
# ---------------------------------------------------------------------------

class TestNormAlbumTitle:
    def test_strips_single_suffix(self):
        result = _norm_album_title("ICE - Single")
        assert "single" not in result.lower()

    def test_strips_ep_suffix_hyphen(self):
        result = _norm_album_title("My Album - EP")
        assert result == "my album"

    def test_strips_extended_play_suffix(self):
        result = _norm_album_title("Waves - Extended Play")
        assert "extended" not in result.lower()

    def test_preserves_normal_title(self):
        assert _norm_album_title("Abbey Road") == "abbey road"

    def test_empty_string(self):
        assert _norm_album_title("") == ""


# ---------------------------------------------------------------------------
# _similarity
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical_strings_return_one(self):
        assert _similarity("hello", "hello") == 1.0

    def test_empty_string_returns_zero(self):
        assert _similarity("", "foo") == 0.0
        assert _similarity("foo", "") == 0.0

    def test_both_empty_returns_zero(self):
        assert _similarity("", "") == 0.0

    def test_similar_strings_between_zero_and_one(self):
        score = _similarity("radiohead", "radioheads")
        assert 0.0 < score < 1.0

    def test_completely_different_strings(self):
        score = _similarity("aaa", "bbb")
        assert score < 0.5


# ---------------------------------------------------------------------------
# _select_itunes_album_art
# ---------------------------------------------------------------------------

class TestSelectItunesAlbumArt:
    def _item(self, artist: str, title: str,
              url: str = "https://example.com/100x100bb.jpg") -> dict:
        return {"artworkUrl100": url, "collectionName": title, "artistName": artist}

    def test_good_match_returns_upscaled_url(self):
        data = {"results": [self._item("Radiohead", "OK Computer")]}
        url = _select_itunes_album_art(data, "Radiohead", "OK Computer")
        assert "600x600bb" in url

    def test_rejects_wrong_artist(self):
        data = {"results": [self._item("Coldplay", "OK Computer")]}
        url = _select_itunes_album_art(data, "Radiohead", "OK Computer")
        assert url == ""

    def test_empty_results(self):
        url = _select_itunes_album_art({"results": []}, "Any", "Album")
        assert url == ""

    def test_none_data(self):
        url = _select_itunes_album_art(None, "Any", "Album")
        assert url == ""

    def test_picks_best_ranked_result(self):
        """Exact match beats a fuzzy match."""
        data = {"results": [
            self._item("Radiohead", "OK Computer", "https://exact/100x100bb.jpg"),
            self._item("Radiohead", "OK Computer OKNOTOK", "https://fuzzy/100x100bb.jpg"),
        ]}
        url = _select_itunes_album_art(data, "Radiohead", "OK Computer")
        assert "exact" in url


# ---------------------------------------------------------------------------
# _mem_cache_put (L1 cache)
# ---------------------------------------------------------------------------

class TestMemCachePut:
    def setup_method(self):
        _mem_cache.clear()

    def test_write_and_read(self):
        _mem_cache_put("key1", "https://example.com/img.jpg", time.time())
        assert "key1" in _mem_cache

    def test_evicts_oldest_when_over_capacity(self):
        """After exceeding _MEM_MAX, the in-memory cache is trimmed."""
        for i in range(_MEM_MAX + 5):
            _mem_cache_put(f"overflow_key_{i}", f"url_{i}", float(i))
        assert len(_mem_cache) <= _MEM_MAX + 1


# ---------------------------------------------------------------------------
# resolve_image — cache tier behaviour
# ---------------------------------------------------------------------------

class TestResolveImage:
    def setup_method(self):
        _mem_cache.clear()

    def test_l1_hit_returns_url_not_pending(self):
        """An L1 cache hit returns (url, False) instantly."""
        _mem_cache_put("cached_artist", "https://example.com/art.jpg", time.time())

        with patch("app.services.image_service._entity_keys",
                   return_value=(["cached_artist"], 100)):
            url, pending = resolve_image("artist", "Cached Artist")

        assert url == "https://example.com/art.jpg"
        assert pending is False

    def test_expired_l1_entry_is_not_returned(self):
        """An L1 entry past its TTL is not served as a hit."""
        _mem_cache["stale_key"] = ("https://example.com/stale.jpg", time.time() - 400)

        with patch("app.services.image_service._entity_keys",
                   return_value=(["stale_key"], 100)):
            with patch("app.services.image_service.rythmx_store.get_image_cache_entry",
                       return_value=None):
                with patch("app.services.image_service._executor.submit"):
                    url, pending = resolve_image("artist", "Stale Artist")

        # stale entry not returned; falls through to L3
        assert url == ""

    def test_full_cache_miss_queues_l3_and_returns_pending(self):
        """A full cache miss submits a background fetch and returns ('', True)."""
        with patch("app.services.image_service._entity_keys",
                   return_value=(["miss_key"], 100)):
            with patch("app.services.image_service.rythmx_store.get_image_cache_entry",
                       return_value=None):
                with patch("app.services.image_service._executor.submit") as mock_submit:
                    url, pending = resolve_image("artist", "Missing Artist")

        assert url == ""
        assert pending is True
        mock_submit.assert_called_once()

    def test_low_confidence_skips_l3_fetch(self):
        """Entities with match_confidence < 85 do not trigger an L3 fetch."""
        with patch("app.services.image_service._entity_keys",
                   return_value=(["low_conf"], 50)):
            with patch("app.services.image_service.rythmx_store.get_image_cache_entry",
                       return_value=None):
                with patch("app.services.image_service._executor.submit") as mock_submit:
                    url, pending = resolve_image("artist", "Low Confidence")

        assert url == ""
        assert pending is False
        mock_submit.assert_not_called()

    def test_not_found_marker_suppresses_l3_retry(self):
        """A 'not_found' marker in L2 prevents re-queuing an L3 fetch."""
        with patch("app.services.image_service._entity_keys",
                   return_value=(["not_found_key"], 100)):
            with patch("app.services.image_service.rythmx_store.get_image_cache_entry",
                       return_value={"image_url": "", "artwork_source": "not_found"}):
                with patch("app.services.image_service._executor.submit") as mock_submit:
                    url, pending = resolve_image("artist", "Not Found Artist")

        assert url == ""
        assert pending is False
        mock_submit.assert_not_called()

    def test_inflight_request_returns_pending_without_duplicate_submit(self):
        """If a fetch is already in-flight, returns pending without submitting again."""
        from app.services.image_service import _in_flight, _in_flight_lock
        key = "inflight_test_key"
        with _in_flight_lock:
            _in_flight.add(key)

        try:
            with patch("app.services.image_service._entity_keys",
                       return_value=([key], 100)):
                with patch("app.services.image_service.rythmx_store.get_image_cache_entry",
                           return_value=None):
                    with patch("app.services.image_service._executor.submit") as mock_submit:
                        url, pending = resolve_image("artist", "In-Flight Artist")

            assert pending is True
            mock_submit.assert_not_called()
        finally:
            with _in_flight_lock:
                _in_flight.discard(key)
