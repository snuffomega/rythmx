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


class TestMusicBrainzGetReleaseWithUrlRels:
    """Test musicbrainz_client.get_release() with url-rels include."""

    def test_parses_tidal_url_relationship(self):
        """Extract Tidal album ID from MB URL relationships."""
        from app.clients import musicbrainz_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "release-mbid-123",
            "release-group": {
                "id": "rg-id-456",
                "first-release-date": "2014-10-27",
            },
            "relationships": [
                {
                    "type": "external links",
                    "url": {
                        "resource": "https://tidal.com/album/37269992",
                        "type": "tidal",
                    },
                },
            ],
        }

        with patch.object(musicbrainz_client._session, "get", return_value=mock_response):
            result = musicbrainz_client.get_release("release-mbid-123", inc="url-rels")

        assert result is not None
        assert result["release_group_id"] == "rg-id-456"
        assert result["first_release_date"] == "2014-10-27"
        assert "url-rels" in result
        assert len(result["url-rels"]) == 1
        assert result["url-rels"][0]["url"] == "https://tidal.com/album/37269992"

    def test_defaults_to_release_groups_include(self):
        """Default inc parameter works without url-rels."""
        from app.clients import musicbrainz_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "release-mbid-123",
            "release-group": {
                "id": "rg-id-456",
                "first-release-date": "2014-10-27",
            },
            "relationships": [],
        }

        with patch.object(musicbrainz_client._session, "get", return_value=mock_response) as mock_get:
            result = musicbrainz_client.get_release("release-mbid-123")

        assert result is not None
        # Verify default inc parameter was used
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[1]["params"]["inc"] == "release-groups"


class TestTidarrPreFetchEnrichMusicBrainzPath:
    """Test Tidarr pre_fetch_enrich via MusicBrainz URL relationships (Path A)."""

    def test_resolves_tidal_id_from_musicbrainz_url_rel(self, monkeypatch):
        """Resolve Tidal ID when MB has url-rel to tidal.com/album."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock musicbrainz_client.get_release to return a Tidal URL relationship
        mock_mb_result = {
            "release_group_id": "rg-123",
            "first_release_date": "2014-10-27",
            "url-rels": [
                {
                    "url": "https://tidal.com/album/37269992",
                    "type": "tidal",
                }
            ],
        }
        monkeypatch.setattr(
            "app.clients.musicbrainz_client.get_release",
            lambda mbid, inc=None: mock_mb_result,
        )

        metadata = {"musicbrainz_release_id": "release-mbid-123"}
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        assert result == {"tidal_album_id": "37269992"}

    def test_returns_empty_when_no_musicbrainz_id(self, monkeypatch):
        """Fallback to empty dict when no MB ID in metadata."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        metadata = {}  # No musicbrainz_release_id
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        assert result == {}

    def test_returns_empty_when_mb_has_no_tidal_url(self, monkeypatch):
        """Fallback to empty dict when MB has no Tidal link."""
        monkeypatch.setenv("TIDARR_URL", "http://tidarr")
        monkeypatch.setenv("TIDARR_API_KEY", "abc")
        downloader = TidarrDownloader()

        # Mock musicbrainz_client to return no Tidal URL
        mock_mb_result = {
            "release_group_id": "rg-123",
            "first_release_date": "2014-10-27",
            "url-rels": [
                {"url": "https://spotify.com/album/xyz", "type": "spotify"},
            ],
        }
        monkeypatch.setattr(
            "app.clients.musicbrainz_client.get_release",
            lambda mbid, inc=None: mock_mb_result,
        )

        metadata = {"musicbrainz_release_id": "release-mbid-123"}
        result = downloader.pre_fetch_enrich("Taylor Swift", "1989", metadata)

        # Path A fails, Path D would be tried, but we mock it to return empty
        # Since we don't mock _get_tidal_token, it will fail gracefully
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
