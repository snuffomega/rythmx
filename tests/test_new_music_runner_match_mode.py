from __future__ import annotations

from app.services.forge import new_music_runner


class _FakeCursor:
    def __init__(self, many=None):
        self._many = many or []

    def fetchall(self):
        return self._many


class _FakeConn:
    def __init__(self, deezer_rows):
        self._deezer_rows = deezer_rows

    def execute(self, _sql, _params=()):
        return _FakeCursor(many=self._deezer_rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_releases_strict_filters_to_primary_artist(monkeypatch):
    monkeypatch.setattr(
        new_music_runner,
        "_connect",
        lambda: _FakeConn([{"name_lower": "test artist", "deezer_artist_id": "42"}]),
    )
    monkeypatch.setattr(
        new_music_runner.music_client,
        "search_artist_candidates_deezer",
        lambda _name, limit=1: [],
    )
    monkeypatch.setattr(
        new_music_runner.music_client,
        "get_artist_albums_deezer",
        lambda _aid: [
            {"id": "a1", "title": "Main Credit Album", "record_type": "album", "release_date": "2099-01-01"},
            {"id": "a2", "title": "Feature Credit Single", "record_type": "single", "release_date": "2099-01-01"},
            {"id": "a3", "title": "Unknown Credit", "record_type": "single", "release_date": "2099-01-01"},
        ],
    )

    credit_map = {
        "a1": {"primary_artist_id": "42", "contributor_ids": ["42"]},
        "a2": {"primary_artist_id": "99", "contributor_ids": ["42", "99"]},
        "a3": None,
    }
    monkeypatch.setattr(
        new_music_runner.music_client,
        "get_deezer_album_credit_info",
        lambda album_id: credit_map.get(album_id),
    )

    _artists, releases, _filtered = new_music_runner.fetch_releases_for_neighbors(
        neighbor_names=["test artist"],
        lookback_days=99999,
        match_mode="strict",
        release_kinds="all",
        ignore_keywords="",
        ignore_artists="",
    )

    assert [r["id"] for r in releases] == ["a1"]


def test_fetch_releases_loose_skips_credit_enforcement(monkeypatch):
    monkeypatch.setattr(
        new_music_runner,
        "_connect",
        lambda: _FakeConn([{"name_lower": "test artist", "deezer_artist_id": "42"}]),
    )
    monkeypatch.setattr(
        new_music_runner.music_client,
        "search_artist_candidates_deezer",
        lambda _name, limit=1: [],
    )
    monkeypatch.setattr(
        new_music_runner.music_client,
        "get_artist_albums_deezer",
        lambda _aid: [
            {"id": "a1", "title": "Main Credit Album", "record_type": "album", "release_date": "2099-01-01"},
            {"id": "a2", "title": "Feature Credit Single", "record_type": "single", "release_date": "2099-01-01"},
        ],
    )

    credit_calls = {"count": 0}

    def _credit_lookup(_album_id):
        credit_calls["count"] += 1
        return None

    monkeypatch.setattr(
        new_music_runner.music_client,
        "get_deezer_album_credit_info",
        _credit_lookup,
    )

    _artists, releases, _filtered = new_music_runner.fetch_releases_for_neighbors(
        neighbor_names=["test artist"],
        lookback_days=99999,
        match_mode="loose",
        release_kinds="all",
        ignore_keywords="",
        ignore_artists="",
    )

    assert sorted(r["id"] for r in releases) == ["a1", "a2"]
    assert credit_calls["count"] == 0
