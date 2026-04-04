from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image

from app.services import artwork_store


def _make_image_bytes(width: int, height: int, color=(0, 255, 0)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _make_three_band_image_bytes() -> bytes:
    """
    600x200 image with vertical thirds:
      left=red, center=green, right=blue.
    A center crop should keep only the green band.
    """
    img = Image.new("RGB", (600, 200), (0, 0, 0))
    for x in range(0, 200):
        for y in range(200):
            img.putpixel((x, y), (255, 0, 0))
    for x in range(200, 400):
        for y in range(200):
            img.putpixel((x, y), (0, 255, 0))
    for x in range(400, 600):
        for y in range(200):
            img.putpixel((x, y), (0, 0, 255))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_get_thumb_returns_square(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(artwork_store, "ARTWORK_DIR", str(tmp_path))

    content_hash = artwork_store.ingest(_make_image_bytes(800, 400))
    payload = artwork_store.get_thumb(content_hash, size=256)

    with Image.open(io.BytesIO(payload)) as thumb:
        assert thumb.size == (256, 256)


def test_get_thumb_center_crops_not_resize_whole_frame(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(artwork_store, "ARTWORK_DIR", str(tmp_path))

    content_hash = artwork_store.ingest(_make_three_band_image_bytes())
    payload = artwork_store.get_thumb(content_hash, size=240)

    with Image.open(io.BytesIO(payload)) as thumb:
        assert thumb.size == (240, 240)
        left = thumb.getpixel((10, 120))
        center = thumb.getpixel((120, 120))
        right = thumb.getpixel((230, 120))

    # All sampled regions should be green-dominant if center-crop is used.
    for px in (left, center, right):
        r, g, b = px[:3]
        assert g > r + 40
        assert g > b + 40
