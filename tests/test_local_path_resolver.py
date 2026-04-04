from __future__ import annotations

from pathlib import Path

from app.services.local_path_resolver import resolve_library_file_path


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")


def test_resolve_library_file_path_exact(tmp_path: Path):
    track = tmp_path / "311" / "311" / "01 - Down.flac"
    _touch(track)

    resolved, mode = resolve_library_file_path(
        str(tmp_path),
        "311/311/01 - Down.flac",
        artist_name="311",
        album_title="311",
    )

    assert mode == "exact"
    assert resolved == str(track)


def test_resolve_library_file_path_fallback_artist_prefix(tmp_path: Path):
    track = tmp_path / "311" / "311 - Evolver" / "01 - Creatures.flac"
    _touch(track)

    resolved, mode = resolve_library_file_path(
        str(tmp_path),
        "311/Evolver/01 - Creatures.flac",
        artist_name="311",
        album_title="Evolver",
    )

    assert mode == "fallback"
    assert resolved == str(track)


def test_resolve_library_file_path_fallback_year_variant(tmp_path: Path):
    track = tmp_path / "311" / "311 - 1996 - 311" / "01 - Down.flac"
    _touch(track)

    resolved, mode = resolve_library_file_path(
        str(tmp_path),
        "311/311/01 - Down.flac",
        artist_name="311",
        album_title="311",
    )

    assert mode == "fallback"
    assert resolved == str(track)


def test_resolve_library_file_path_ambiguous_returns_none(tmp_path: Path):
    track_a = tmp_path / "311" / "Evolver" / "01 - Creatures.flac"
    track_b = tmp_path / "311" / "311 - Evolver" / "01 - Creatures.flac"
    _touch(track_a)
    _touch(track_b)

    resolved, mode = resolve_library_file_path(
        str(tmp_path),
        "311/Evolver Alt/01 - Creatures.flac",
        artist_name="311",
        album_title="Evolver",
    )

    assert resolved is None
    assert mode == "ambiguous"
