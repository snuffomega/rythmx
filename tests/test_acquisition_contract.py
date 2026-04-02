from fastapi.responses import JSONResponse

from app.routes import acquisition


def test_acquisition_queue_add_accepts_canonical_payload(monkeypatch):
    captured = {}

    def fake_add_to_queue(**kwargs):
        captured.update(kwargs)
        return 101

    monkeypatch.setattr(acquisition.rythmx_store, "add_to_queue", fake_add_to_queue)

    result = acquisition.acquisition_queue_add(
        {
            "artist_name": "  Massive Attack  ",
            "album_title": "  Mezzanine  ",
            "kind": "album",
            "release_date": "1998-04-20",
            "source": "manual",
        }
    )

    assert result == {"status": "ok", "queue_id": 101}
    assert captured["artist_name"] == "Massive Attack"
    assert captured["album_title"] == "Mezzanine"
    assert captured["kind"] == "album"
    assert captured["release_date"] == "1998-04-20"
    assert captured["source"] == "manual"
    assert captured["requested_by"] == "manual"


def test_acquisition_queue_add_accepts_legacy_frontend_payload(monkeypatch):
    captured = {}

    def fake_add_to_queue(**kwargs):
        captured.update(kwargs)
        return 202

    monkeypatch.setattr(acquisition.rythmx_store, "add_to_queue", fake_add_to_queue)

    result = acquisition.acquisition_queue_add(
        {
            "artist": "  Boards of Canada  ",
            "album": "  Tomorrow's Harvest  ",
            "kind": "album",
        }
    )

    assert result == {"status": "ok", "queue_id": 202}
    assert captured["artist_name"] == "Boards of Canada"
    assert captured["album_title"] == "Tomorrow's Harvest"


def test_acquisition_queue_add_rejects_missing_artist_or_album():
    result = acquisition.acquisition_queue_add({"artist": "Only Artist"})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    assert b"artist_name/album_title" in result.body

