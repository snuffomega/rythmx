"""
tests/test_deezer_bpm.py — Deezer BPM enrichment unit tests.

Mocks all network and DB I/O. Tests cover:
  - _fetch_deezer_album_tracks: happy path, empty response, error
  - enrich_deezer_bpm: tracks updated, no deezer tracks (not_found), no albums to process
  - get_deezer_bpm_status: counts from enrichment_meta + lib_tracks

Run with: pytest tests/test_deezer_bpm.py -v
"""
import pytest
import json
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deezer_track_response(tracks: list[dict]) -> dict:
    """Build a fake Deezer /album/{id}/tracks response."""
    return {"data": tracks}


def _album_row(album_id="alb1", deezer_id="dz99", artist_name="Ballyhoo!", title="Daydream"):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "id": album_id, "deezer_id": deezer_id,
        "artist_name": artist_name, "title": title,
    }[k]
    return row


def _track_row(track_id="t1", title_lower="some song"):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {"id": track_id, "title_lower": title_lower}[k]
    return row


# ---------------------------------------------------------------------------
# _fetch_deezer_album_tracks
# ---------------------------------------------------------------------------

class TestFetchDeezerAlbumTracks:
    def test_returns_bpm_list_on_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_deezer_track_response([
            {"title": "Some Song", "bpm": 120.0},
            {"title": "Another Song", "bpm": 95.5},
        ])
        mock_resp.raise_for_status.return_value = None

        with patch("app.services.library_service.time") as mock_time, \
             patch("requests.get", return_value=mock_resp):
            mock_time.time.return_value = 9999.0
            from app.services.library_service import _fetch_deezer_album_tracks
            result = _fetch_deezer_album_tracks("dz99")

        assert len(result) == 2
        assert result[0] == {"title": "Some Song", "bpm": 120.0}
        assert result[1] == {"title": "Another Song", "bpm": 95.5}

    def test_filters_zero_bpm(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_deezer_track_response([
            {"title": "Track A", "bpm": 0},
            {"title": "Track B", "bpm": 130.0},
        ])
        mock_resp.raise_for_status.return_value = None

        with patch("app.services.library_service.time") as mock_time, \
             patch("requests.get", return_value=mock_resp):
            mock_time.time.return_value = 9999.0
            from app.services.library_service import _fetch_deezer_album_tracks
            result = _fetch_deezer_album_tracks("dz99")

        assert len(result) == 1
        assert result[0]["title"] == "Track B"

    def test_returns_empty_on_network_error(self):
        with patch("app.services.library_service.time") as mock_time, \
             patch("requests.get", side_effect=Exception("timeout")):
            mock_time.time.return_value = 9999.0
            from app.services.library_service import _fetch_deezer_album_tracks
            result = _fetch_deezer_album_tracks("dz99")

        assert result == []

    def test_returns_empty_on_no_data(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status.return_value = None

        with patch("app.services.library_service.time") as mock_time, \
             patch("requests.get", return_value=mock_resp):
            mock_time.time.return_value = 9999.0
            from app.services.library_service import _fetch_deezer_album_tracks
            result = _fetch_deezer_album_tracks("dz99")

        assert result == []


# ---------------------------------------------------------------------------
# enrich_deezer_bpm
# ---------------------------------------------------------------------------

class TestEnrichDeezerBpm:
    def _make_conn(self, album_rows, track_rows):
        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.execute.return_value.fetchall.side_effect = [album_rows, track_rows]
        return conn

    def test_enriches_matching_tracks(self):
        album = _album_row()
        track = _track_row(track_id="t1", title_lower="some song")

        deezer_tracks = [{"title": "Some Song", "bpm": 120.0}]

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        # First call: album select; second: track select
        conn.execute.return_value.fetchall.side_effect = [[album], [track]]

        with patch("app.services.library_service._connect", return_value=conn), \
             patch("app.services.library_service._fetch_deezer_album_tracks",
                   return_value=deezer_tracks), \
             patch("app.services.library_service._write_enrichment_meta"):
            from app.services.library_service import enrich_deezer_bpm
            result = enrich_deezer_bpm(batch_size=10)

        assert result["enriched_tracks"] == 1
        assert result["enriched_albums"] == 1
        assert result["failed"] == 0

    def test_skips_album_when_no_deezer_tracks(self):
        album = _album_row()

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.execute.return_value.fetchall.return_value = [album]

        with patch("app.services.library_service._connect", return_value=conn), \
             patch("app.services.library_service._fetch_deezer_album_tracks",
                   return_value=[]), \
             patch("app.services.library_service._write_enrichment_meta"):
            from app.services.library_service import enrich_deezer_bpm
            result = enrich_deezer_bpm(batch_size=10)

        assert result["skipped"] == 1
        assert result["enriched_tracks"] == 0

    def test_returns_zero_when_nothing_to_process(self):
        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.execute.return_value.fetchall.return_value = []

        with patch("app.services.library_service._connect", return_value=conn):
            from app.services.library_service import enrich_deezer_bpm
            result = enrich_deezer_bpm(batch_size=10)

        assert result == {"enriched_tracks": 0, "enriched_albums": 0,
                          "failed": 0, "skipped": 0, "remaining": 0}

    def test_handles_db_read_error_gracefully(self):
        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.execute.side_effect = Exception("DB locked")

        with patch("app.services.library_service._connect", return_value=conn):
            from app.services.library_service import enrich_deezer_bpm
            result = enrich_deezer_bpm(batch_size=10)

        assert result["remaining"] == -1
        assert "error" in result


# ---------------------------------------------------------------------------
# get_deezer_bpm_status
# ---------------------------------------------------------------------------

class TestGetDeezerBpmStatus:
    def test_returns_counts(self):
        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        # total_albums=10, enriched_albums=4, enriched_tracks=55, total_tracks=120
        conn.execute.return_value.fetchone.side_effect = [(10,), (4,), (55,), (120,)]

        with patch("app.services.library_service._connect", return_value=conn), \
             patch("app.services.library_service.rythmx_store") as mock_store:
            mock_store.get_setting.return_value = "2026-03-08T00:00:00"
            from app.services.library_service import get_deezer_bpm_status
            result = get_deezer_bpm_status()

        assert result["total_albums"] == 10
        assert result["enriched_albums"] == 4
        assert result["enriched_tracks"] == 55
        assert result["total_tracks"] == 120
        assert result["last_run"] == "2026-03-08T00:00:00"

    def test_returns_zeros_on_db_error(self):
        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.execute.side_effect = Exception("no table")

        with patch("app.services.library_service._connect", return_value=conn), \
             patch("app.services.library_service.rythmx_store") as mock_store:
            mock_store.get_setting.return_value = None
            from app.services.library_service import get_deezer_bpm_status
            result = get_deezer_bpm_status()

        assert result["total_albums"] == 0
        assert result["enriched_tracks"] == 0
