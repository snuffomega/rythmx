from fastapi.responses import JSONResponse

from app.routes import images


def test_images_resolve_batch_resolves_items(monkeypatch):
    import app.services.image_service as image_service

    calls = []

    def fake_resolve(entity_type: str, name: str, artist: str):
        calls.append((entity_type, name, artist))
        return (f"https://img.test/{entity_type}/{name}", False)

    monkeypatch.setattr(image_service, "resolve_image", fake_resolve)

    result = images.images_resolve_batch(
        {
            "items": [
                {"id": "a:1", "type": "album", "name": "Blue Train", "artist": "Coltrane"},
                {"id": "r:2", "type": "artist", "name": "Massive Attack"},
            ]
        }
    )

    assert "items" in result
    assert len(result["items"]) == 2
    assert result["items"][0]["id"] == "a:1"
    assert result["items"][0]["image_url"].startswith("https://img.test/album/")
    assert result["items"][1]["id"] == "r:2"
    assert calls == [
        ("album", "Blue Train", "Coltrane"),
        ("artist", "Massive Attack", ""),
    ]


def test_images_resolve_batch_rejects_non_list_items():
    result = images.images_resolve_batch({"items": "nope"})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400

