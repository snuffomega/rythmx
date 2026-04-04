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
