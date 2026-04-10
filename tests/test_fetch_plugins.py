from __future__ import annotations

import os
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

from app.plugins import DownloadArtifact
from plugins.plugin_file_mover import UniversalFileMover
from plugins.plugin_tidarr import TidarrDownloader


def _item_xml(*, artist: str, album: str, year: str = "2026", quality: str = "lossless") -> str:
    return (
        "<item>"
        f"<title>{artist} - {album}</title>"
        f"<guid>12345-{quality}</guid>"
        f"<attr name='artist' value='{artist}' />"
        f"<attr name='album' value='{album}' />"
        f"<attr name='year' value='{year}' />"
        "<enclosure url='http://example.local/download/12345/file.nzb'/>"
        "</item>"
    )


def test_tidarr_pick_best_item_prefers_exact_match(monkeypatch):
    monkeypatch.setenv("TIDARR_URL", "http://tidarr")
    monkeypatch.setenv("TIDARR_API_KEY", "abc")
    downloader = TidarrDownloader()

    exact = ET.fromstring(_item_xml(artist="Jason Mraz", album="His Eye Is On The Sparrow"))
    wrong = ET.fromstring(_item_xml(artist="Jason Mraz", album="Love Is a Four Letter Word"))

    picked = downloader._pick_best_item(
        [wrong, exact],
        "Jason Mraz",
        "His Eye Is On The Sparrow",
        {"release_date": "2026-04-09"},
    )
    assert picked is exact


def test_tidarr_pick_best_item_rejects_low_confidence(monkeypatch):
    monkeypatch.setenv("TIDARR_URL", "http://tidarr")
    monkeypatch.setenv("TIDARR_API_KEY", "abc")
    downloader = TidarrDownloader()

    unrelated = ET.fromstring(_item_xml(artist="Different Artist", album="Unrelated Album"))
    picked = downloader._pick_best_item(
        [unrelated],
        "Jason Mraz",
        "His Eye Is On The Sparrow",
        {"release_date": "2026-04-09"},
    )
    assert picked is None


def test_tidarr_preview_match_marks_search_inconsistent_with_explicit_id(monkeypatch):
    monkeypatch.setenv("TIDARR_URL", "http://tidarr")
    monkeypatch.setenv("TIDARR_API_KEY", "abc")
    downloader = TidarrDownloader()
    monkeypatch.setattr(downloader, "_search_candidates", lambda _artist, _album: [])

    result = downloader.preview_match(
        "Jason Mraz",
        "His Eye Is On The Sparrow",
        {"tidal_album_id": "510517322", "release_date": "2026-01-01"},
    )

    assert result["match_status"] == "search_inconsistent"
    selected = result.get("selected") or {}
    assert str(selected.get("tidal_id")) == "510517322"


def test_tidarr_submit_with_match_returns_unresolved_when_no_candidate(monkeypatch):
    monkeypatch.setenv("TIDARR_URL", "http://tidarr")
    monkeypatch.setenv("TIDARR_API_KEY", "abc")
    downloader = TidarrDownloader()
    monkeypatch.setattr(
        downloader,
        "preview_match",
        lambda _artist, _album, _metadata: {
            "match_status": "unresolved",
            "match_strategy": "search_score",
            "match_confidence": 0.0,
            "match_reasons": ["No candidates"],
            "candidates": [],
            "selected": None,
        },
    )

    out = downloader.submit_with_match("Artist", "Album", {})
    assert out["status"] == "unresolved"
    assert out["job_id"] == ""


def test_file_mover_normalizes_permissions(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "downloads"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "01-track.flac"
    source_file.write_bytes(b"fLaC")
    source_file.chmod(0o600)

    dest_root = tmp_path / "music"
    monkeypatch.setenv("FILE_MOVER_DEST", str(dest_root))
    monkeypatch.setenv("FILE_MOVER_DIR_MODE", "775")
    monkeypatch.setenv("FILE_MOVER_FILE_MODE", "664")

    mover = UniversalFileMover()
    artifact = DownloadArtifact(
        job_id="job_1",
        artist="Jason Mraz",
        album="His Eye Is On The Sparrow",
        source_dir=str(source_dir),
        files=[str(source_file)],
    )

    out = mover.organize(artifact)
    assert out.dest_dir is not None
    copied_path = Path(out.dest_dir) / source_file.name
    assert copied_path.exists()

    if os.name != "nt":
        file_mode = stat.S_IMODE(copied_path.stat().st_mode)
        dir_mode = stat.S_IMODE(Path(out.dest_dir).stat().st_mode)
        assert file_mode == 0o664
        assert dir_mode == 0o775
