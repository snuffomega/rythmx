"""Tests for pre-fetch enrichment functionality (Phases 2-4)."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from app.services.fetch_pipeline import _validate_enrichment_result
from plugins.plugin_tidarr import TidarrDownloader


class TestValidateEnrichmentResult:
    """Test _validate_enrichment_result validation logic."""

    def test_accepts_safe_ids(self):
        """Accept safe ID strings."""
        result = _validate_enrichment_result({
            "tidal_album_id": "37269992",
            "deezer_album_id": "123456",
            "musicbrainz_release_id": "abc-def-123",
        })
        assert result == {
            "tidal_album_id": "37269992",
            "deezer_album_id": "123456",
            "musicbrainz_release_id": "abc-def-123",
        }

    def test_accepts_safe_primitives(self):
        """Accept integers, floats, booleans, and None."""
        result = _validate_enrichment_result({
            "tidal_album_id": "123",
            "release_date": "2014-10-27",
            "track_count": 13,
        })
        assert result["tidal_album_id"] == "123"
        assert result["release_date"] == "2014-10-27"
        assert result["track_count"] == 13

    def test_rejects_unknown_keys(self):
        """Reject keys not in allowlist."""
        result = _validate_enrichment_result({
            "tidal_album_id": "123",
            "random_key": "value",
            "another_unknown": "data",
        })
        assert "tidal_album_id" in result
        assert "random_key" not in result
        assert "another_unknown" not in result

    def test_rejects_jwt_token_pattern(self):
        """Reject JWT-like values (eyJ...)."""
        result = _validate_enrichment_result({
            "tidal_album_id": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        })
        assert "tidal_album_id" not in result

    def test_rejects_api_key_pattern(self):
        """Reject long hex values that look like API keys."""
        result = _validate_enrichment_result({
            "tidal_album_id": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        })
        assert "tidal_album_id" not in result

    def test_rejects_url_values(self):
        """Reject URLs (http/https)."""
        result = _validate_enrichment_result({
            "tidal_album_id": "https://api.tidal.com/album/123?key=secret",
        })
        assert "tidal_album_id" not in result

    def test_rejects_non_primitive_types(self):
        """Reject lists, dicts, objects."""
        result = _validate_enrichment_result({
            "tidal_album_id": "123",
            "metadata": {"nested": "dict"},  # dict not allowed
            "items": [1, 2, 3],  # list not allowed
        })
        assert "tidal_album_id" in result
        assert "metadata" not in result
        assert "items" not in result

    def test_returns_empty_dict_for_non_dict_input(self):
        """Handle non-dict input gracefully."""
        result = _validate_enrichment_result("not a dict")  # type: ignore
        assert result == {}

        result = _validate_enrichment_result(None)  # type: ignore
        assert result == {}

        result = _validate_enrichment_result([])  # type: ignore
        assert result == {}


class TestTidarrPreFetchEnrichCachedArtistPath:
    """Test Tidarr pre_fetch_enrich via cached artist ID (Path A)."""

    def test_resolves_via_cached_artist_id(self, monkeypatch):
        """Resolve Tidal ID when artist has cached tidal_artist_id in DB."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock DB lookup to return cached artist ID
        def mock_connect():
            from unittest.mock import MagicMock
            conn = MagicMock()
            # Return row with tidal_artist_id
            conn.execute.return_value.fetchone.return_value = (1, "taylor_swift_artist_id")
            return conn.__enter__.return_value

        monkeypatch.setattr("app.db.rythmx_store._connect", mock_connect)

        # Mock token fetching
        monkeypatch.setattr(downloader, "_get_tidal_token", lambda: "mock_token")

        # Mock artist releases and evaluation
        tidal_album = {
            "id": 37269992,
            "title": "1989",
            "artists": [{"name": "Taylor Swift"}],
            "releaseDate": "2014-10-27",
            "numberOfTracks": 13,
        }
        monkeypatch.setattr(
            downloader,
            "_get_artist_releases",
            lambda token, artist_id, filter_type: [tidal_album],
        )

        monkeypatch.setattr(
            "app.services.fetch_matching.evaluate_tidarr_candidates",
            lambda **kwargs: {
                "match_status": "confident",
                "match_confidence": 0.95,
                "selected": {"tidal_id": "37269992"},
            },
        )

        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", {})
        assert result == {"tidal_album_id": "37269992"}

    def test_returns_empty_when_no_cached_artist(self, monkeypatch):
        """Falls through to fresh search if no cached artist ID."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock DB lookup to return no cached ID
        def mock_connect():
            from unittest.mock import MagicMock
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = None
            return conn.__enter__.return_value

        monkeypatch.setattr("app.db.rythmx_store._connect", mock_connect)
        monkeypatch.setattr(downloader, "_get_tidal_token", lambda: None)

        result = downloader.pre_fetch_enrich("Unknown Artist", "Unknown Album", {})
        assert result == {}


class TestTidarrPreFetchEnrichArtistCatalogPath:
    """Test Tidarr pre_fetch_enrich via artist catalog search (Path E)."""

    def test_resolves_tidal_id_from_artist_catalog(self, monkeypatch):
        """Resolve Tidal ID when artist catalog search finds confident match."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock token fetching
        monkeypatch.setattr(
            downloader,
            "_get_tidal_token",
            lambda: "mock_tidal_token",
        )

        # Mock artist search and matching
        monkeypatch.setattr(
            downloader,
            "_search_and_match_artist",
            lambda token, artist: "taylor_swift_artist_id",
        )

        # Mock artist releases fetching
        tidal_album = {
            "id": 37269992,
            "title": "1989",
            "artists": [{"name": "Taylor Swift"}],
            "releaseDate": "2014-10-27",
            "numberOfTracks": 13,
            "version": None,
        }
        monkeypatch.setattr(
            downloader,
            "_get_artist_releases",
            lambda token, artist_id, filter_type: [tidal_album],
        )

        # Mock evaluate_tidarr_candidates to return confident match
        from app.services.fetch_matching import evaluate_tidarr_candidates
        monkeypatch.setattr(
            "app.services.fetch_matching.evaluate_tidarr_candidates",
            lambda **kwargs: {
                "match_status": "confident",
                "match_confidence": 0.95,
                "selected": {
                    "tidal_id": "37269992",
                    "artist": "Taylor Swift",
                    "album": "1989",
                },
            },
        )

        metadata = {"release_date": "2014-10-27", "track_count": 13}
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        assert result == {"tidal_album_id": "37269992"}

    def test_returns_empty_when_no_token_available(self, monkeypatch):
        """Fallback to empty dict when Tidal token not available."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock _get_tidal_token to return None
        monkeypatch.setattr(downloader, "_get_tidal_token", lambda: None)

        metadata = {}
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        assert result == {}

    def test_returns_empty_when_no_confident_artist_match(self, monkeypatch):
        """Fallback to empty dict when artist search doesn't find confident match."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        monkeypatch.setattr(downloader, "_get_tidal_token", lambda: "mock_token")
        monkeypatch.setattr(downloader, "_search_and_match_artist", lambda token, artist: None)

        metadata = {}
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        assert result == {}


class TestTokenNeverPersisted:
    """Test that Tidal tokens are never persisted in DB or logs."""

    def test_get_tidal_token_returns_fresh_token_not_cached(self, monkeypatch):
        """Token is fetched fresh each time, not stored."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "tiddl_config": {"auth": {"token": f"fresh_token_{call_count}"}}
            }
            return mock_resp

        monkeypatch.setattr(downloader._session, "get", mock_get)

        token1 = downloader._get_tidal_token()
        token2 = downloader._get_tidal_token()

        # Tokens should be different (fresh fetch each time)
        assert token1 == "fresh_token_1"
        assert token2 == "fresh_token_2"
        assert call_count == 2

    def test_enrichment_result_does_not_contain_token(self, monkeypatch):
        """Pre-fetch enrichment result only contains safe IDs, never tokens."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        monkeypatch.setattr(downloader, "_get_tidal_token", lambda: "real_tidal_token_xyz123")
        monkeypatch.setattr(
            downloader,
            "_search_and_match_artist",
            lambda token, artist: "taylor_swift_artist_id",
        )
        monkeypatch.setattr(
            downloader,
            "_get_artist_releases",
            lambda token, artist_id, filter_type: [
                {
                    "id": 37269992,
                    "title": "1989",
                    "artists": [{"name": "Taylor Swift"}],
                    "releaseDate": "2014-10-27",
                    "numberOfTracks": 13,
                }
            ],
        )

        monkeypatch.setattr(
            "app.services.fetch_matching.evaluate_tidarr_candidates",
            lambda **kwargs: {
                "match_status": "confident",
                "match_confidence": 0.95,
                "selected": {"tidal_id": "37269992"},
            },
        )

        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", {})

        # Result should only have safe IDs
        assert "tidal_album_id" in result
        assert result["tidal_album_id"] == "37269992"
        # No token-like values
        for key, value in result.items():
            assert "token" not in str(key).lower()
            assert "xyz123" not in str(value)
