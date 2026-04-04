"""
artwork_store.py - Local content-addressed artwork storage and thumbnail cache.

Layout under ARTWORK_DIR:
  originals/{aa}/{bb}/{sha256}      raw source bytes
  cache/{size}_{sha256}.webp        generated WebP thumbnails
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path

from app.config import ARTWORK_DIR

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MIN_SIZE = 32
_MAX_SIZE = 2048
_THUMB_CACHE_VERSION = "v2"


def _root() -> Path:
    return Path(ARTWORK_DIR)


def originals_dir() -> Path:
    return _root() / "originals"


def cache_dir() -> Path:
    return _root() / "cache"


def ensure_artwork_dirs() -> None:
    """Ensure ARTWORK_DIR plus expected subdirectories exist."""
    originals_dir().mkdir(parents=True, exist_ok=True)
    cache_dir().mkdir(parents=True, exist_ok=True)


def _normalize_size(size: int) -> int:
    return max(_MIN_SIZE, min(_MAX_SIZE, int(size)))


def _validate_hash(content_hash: str) -> str:
    h = (content_hash or "").strip().lower()
    if not _HASH_RE.match(h):
        raise ValueError("Invalid artwork hash")
    return h


def get_original_path(content_hash: str) -> Path:
    """Return sharded path for an original artwork blob by SHA-256 hash."""
    h = _validate_hash(content_hash)
    return originals_dir() / h[:2] / h[2:4] / h


def get_thumb_cache_path(content_hash: str, size: int) -> Path:
    h = _validate_hash(content_hash)
    s = _normalize_size(size)
    return cache_dir() / f"{_THUMB_CACHE_VERSION}_{s}_{h}.webp"


def ingest(raw_bytes: bytes) -> str:
    """
    Store raw image bytes in content-addressed originals storage.
    Returns SHA-256 hex hash.
    """
    if not raw_bytes:
        raise ValueError("Cannot ingest empty artwork bytes")

    ensure_artwork_dirs()

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    original_path = get_original_path(content_hash)

    if original_path.exists():
        return content_hash

    original_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write to avoid partial files under concurrent ingest calls.
    tmp_path = original_path.with_suffix(".tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(raw_bytes)
    os.replace(tmp_path, original_path)
    return content_hash


def get_thumb(content_hash: str, size: int = 300) -> bytes:
    """
    Return cached square thumbnail bytes, generating WebP on cache miss.

    Raises:
      FileNotFoundError - original hash not present
      ValueError        - hash invalid or source image unreadable
    """
    ensure_artwork_dirs()

    h = _validate_hash(content_hash)
    s = _normalize_size(size)
    thumb_path = get_thumb_cache_path(h, s)

    if thumb_path.exists():
        return thumb_path.read_bytes()

    original_path = get_original_path(h)
    if not original_path.exists():
        raise FileNotFoundError(f"Artwork hash not found: {h}")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("Pillow is required for artwork thumbnail generation") from exc

    try:
        with Image.open(original_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            width, height = img.size
            if width <= 0 or height <= 0:
                raise ValueError("Source artwork has invalid dimensions")

            # Center-crop to square first, then resize to requested size.
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            right = left + side
            bottom = top + side
            img = img.crop((left, top, right, bottom))
            if img.size != (s, s):
                img = img.resize((s, s), Image.Resampling.LANCZOS)

            out = io.BytesIO()
            img.save(out, format="WEBP", quality=86, method=6)
            data = out.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Could not decode source artwork: {exc}") from exc

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = thumb_path.with_suffix(".tmp")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, thumb_path)

    return data
