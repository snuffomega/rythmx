from app.services import playlist_importer


def test_extract_deezer_playlist_id_from_direct_url():
    url = "https://www.deezer.com/us/playlist/123456789"
    assert playlist_importer._extract_deezer_playlist_id(url) == "123456789"


def test_extract_deezer_playlist_id_from_short_link(monkeypatch):
    short_url = "https://link.deezer.com/s/32SSeJjwoWFT0Xw4AGSyg"
    monkeypatch.setattr(
        playlist_importer,
        "_resolve_redirect_url",
        lambda _url: "https://www.deezer.com/us/playlist/987654321",
    )
    assert playlist_importer._extract_deezer_playlist_id(short_url) == "987654321"


def test_normalize_owned_track_id_handles_reader_dict_shape():
    result = playlist_importer._normalize_owned_track_id(
        {"id": "track-123", "title": "Song"}
    )
    assert result == "track-123"


def test_normalize_owned_track_id_handles_string_and_none():
    assert playlist_importer._normalize_owned_track_id("abc") == "abc"
    assert playlist_importer._normalize_owned_track_id(None) is None


def test_import_from_deezer_uses_tracks_endpoint_pagination(monkeypatch):
    class _FakeResp:
        def __init__(self, payload: dict):
            self._payload = payload

        def read(self):
            import json

            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    calls: list[str] = []

    def _fake_urlopen(url, timeout=15):
        _ = timeout
        calls.append(str(url))
        u = str(url)
        if u.endswith("/playlist/123456789"):
            return _FakeResp(
                {
                    "title": "Big Deezer Playlist",
                    "tracks": {"data": [{"id": "embedded_only_should_not_drive_count"}]},
                }
            )
        if "playlist/123456789/tracks" in u and "index=0" in u:
            return _FakeResp(
                {
                    "data": [
                        {"id": "1", "title": "Song 1", "artist": {"name": "A1"}, "album": {"title": "AL1"}},
                        {"id": "2", "title": "Song 2", "artist": {"name": "A2"}, "album": {"title": "AL2"}},
                    ],
                    "next": "https://api.deezer.com/playlist/123456789/tracks?limit=100&index=100",
                }
            )
        if "playlist/123456789/tracks" in u and "index=100" in u:
            return _FakeResp(
                {
                    "data": [
                        {"id": "3", "title": "Song 3", "artist": {"name": "A3"}, "album": {"title": "AL3"}},
                    ],
                    "next": None,
                }
            )
        raise AssertionError(f"Unexpected URL: {u}")

    class _FakeReader:
        @staticmethod
        def check_owned_deezer(_track_id):
            return None

        @staticmethod
        def find_track_by_name(_artist_name, _track_name):
            return None

    monkeypatch.setattr(playlist_importer.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr("app.db.get_library_reader", lambda: _FakeReader())

    result = playlist_importer.import_from_deezer("https://www.deezer.com/us/playlist/123456789")
    assert result["status"] == "ok"
    assert result["name"] == "Big Deezer Playlist"
    assert result["track_count"] == 3
    assert len(result["tracks"]) == 3
    assert any("/playlist/123456789/tracks?limit=100&index=0" in c for c in calls)
    assert any("/playlist/123456789/tracks?limit=100&index=100" in c for c in calls)
