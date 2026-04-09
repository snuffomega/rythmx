from __future__ import annotations

import os
from pathlib import Path

from app.db import rythmx_store
from app.services import fetch_pipeline


class _ReaderOwned:
    @staticmethod
    def check_album_owned(*_args, **_kwargs):
        return {"match": True}


class _NoopTagger:
    name = "noop"

    @staticmethod
    def tag(artifact):
        return artifact


class _NoopFileHandler:
    name = "noop"

    @staticmethod
    def organize(artifact):
        return artifact


def _build_with_missing_album(name: str, *, artist: str = "Artist A", album: str = "Album A") -> dict:
    return rythmx_store.create_forge_build(
        name=name,
        source="new_music",
        status="ready",
        run_mode="build",
        track_list=[
            {"artist_name": artist, "title": album, "in_library": 0},
            {"artist_name": artist, "title": album, "in_library": 0},  # duplicate
            {"artist_name": "Owned Artist", "title": "Owned Album", "in_library": 1},
        ],
        summary={},
    )


def test_start_fetch_run_creates_deduped_tasks_and_jobs(tmp_db, monkeypatch):  # noqa: ARG001
    class _Downloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            return "tidarr_nzo_1"

    monkeypatch.setattr("app.plugins.get_downloader", lambda: _Downloader())

    build = _build_with_missing_album("Fetch Build Dedup")
    run = fetch_pipeline.start_fetch_run(build["id"])

    assert run["id"]
    assert run["total_tasks"] == 1
    assert run["submission"]["submitted"] == 1
    tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert len(tasks) == 1
    assert tasks[0]["stage"] == "submitted"

    jobs = rythmx_store.get_download_jobs_for_build(build["id"])
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "tidarr_nzo_1"


def test_poll_once_flows_to_in_library(tmp_db, monkeypatch, tmp_path):  # noqa: ARG001
    storage = Path(tmp_path) / "downloads" / "Artist A" / "Album A"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "01-track.flac").write_bytes(b"fLaC")

    class _Downloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            return "tidarr_nzo_2"

        @staticmethod
        def poll_history(limit: int = 400):  # noqa: ARG004
            return [{"nzo_id": "tidarr_nzo_2", "status": "Completed", "storage": str(storage)}]

        @staticmethod
        def poll_queue():
            return []

        @staticmethod
        def translate_path(path: str) -> str:
            return path

    monkeypatch.setattr("app.plugins.get_downloader", lambda: _Downloader())
    monkeypatch.setattr("app.plugins.get_tagger", lambda: _NoopTagger())
    monkeypatch.setattr("app.plugins.get_file_handler", lambda: _NoopFileHandler())
    monkeypatch.setattr("app.services.fetch_pipeline.get_library_reader", lambda: _ReaderOwned())
    monkeypatch.setattr(
        "app.services.enrichment.sync.sync_library",
        lambda: {"artist_count": 1, "album_count": 1, "track_count": 1},
    )

    build = _build_with_missing_album("Fetch Build E2E")
    run = fetch_pipeline.start_fetch_run(build["id"])
    tick = fetch_pipeline.poll_once()

    assert tick["checked"] >= 1
    latest = fetch_pipeline.get_fetch_run(run["id"])
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["in_library"] == 1

    tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert tasks[0]["stage"] == "in_library"
    assert os.path.isdir(tasks[0]["storage_path"])


def test_poll_once_resolves_local_prefix_candidate_when_storage_path_is_provider_scoped(
    tmp_db, monkeypatch, tmp_path
):  # noqa: ARG001
    visible_root = Path(tmp_path) / "downloads"
    visible_storage = visible_root / "tidarr_nzo_3"
    visible_storage.mkdir(parents=True, exist_ok=True)
    (visible_storage / "01-track.flac").write_bytes(b"fLaC")
    provider_storage = "/downloads/tidarr_nzo_3"

    class _Downloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            return "tidarr_nzo_3"

        @staticmethod
        def poll_history(limit: int = 400):  # noqa: ARG004
            return [{"nzo_id": "tidarr_nzo_3", "status": "Completed", "storage": provider_storage}]

        @staticmethod
        def poll_queue():
            return []

        @staticmethod
        def translate_path(path: str) -> str:
            return path

    monkeypatch.setenv("FILE_MOVER_LOCAL_PREFIX", str(visible_root))
    monkeypatch.delenv("FILE_MOVER_TIDARR_PREFIX", raising=False)
    monkeypatch.setattr("app.plugins.get_downloader", lambda: _Downloader())
    monkeypatch.setattr("app.plugins.get_tagger", lambda: _NoopTagger())
    monkeypatch.setattr("app.plugins.get_file_handler", lambda: _NoopFileHandler())
    monkeypatch.setattr("app.services.fetch_pipeline.get_library_reader", lambda: _ReaderOwned())
    monkeypatch.setattr(
        "app.services.enrichment.sync.sync_library",
        lambda: {"artist_count": 1, "album_count": 1, "track_count": 1},
    )

    build = _build_with_missing_album("Fetch Path Fallback")
    run = fetch_pipeline.start_fetch_run(build["id"])
    fetch_pipeline.poll_once()

    latest = fetch_pipeline.get_fetch_run(run["id"])
    assert latest is not None
    assert latest["status"] == "completed"
    tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert tasks[0]["stage"] == "in_library"
    assert os.path.normpath(str(tasks[0]["source_dir"])) == os.path.normpath(str(visible_storage))


def test_poll_once_missing_source_error_includes_attempted_paths(tmp_db, monkeypatch, tmp_path):  # noqa: ARG001
    visible_root = Path(tmp_path) / "downloads"
    visible_root.mkdir(parents=True, exist_ok=True)
    provider_storage = "/downloads/tidarr_nzo_missing"

    class _Downloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            return "tidarr_nzo_missing"

        @staticmethod
        def poll_history(limit: int = 400):  # noqa: ARG004
            return [{"nzo_id": "tidarr_nzo_missing", "status": "Completed", "storage": provider_storage}]

        @staticmethod
        def poll_queue():
            return []

        @staticmethod
        def translate_path(path: str) -> str:
            return path

    monkeypatch.setenv("FILE_MOVER_LOCAL_PREFIX", str(visible_root))
    monkeypatch.delenv("FILE_MOVER_TIDARR_PREFIX", raising=False)
    monkeypatch.setattr("app.plugins.get_downloader", lambda: _Downloader())
    monkeypatch.setattr("app.plugins.get_tagger", lambda: _NoopTagger())
    monkeypatch.setattr("app.plugins.get_file_handler", lambda: _NoopFileHandler())

    build = _build_with_missing_album("Fetch Path Missing")
    run = fetch_pipeline.start_fetch_run(build["id"])
    fetch_pipeline.poll_once()

    tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert tasks[0]["stage"] == "failed"
    message = str(tasks[0].get("error_message") or "")
    assert "Source path not accessible" in message
    assert "tried:" in message
    assert os.path.normpath(str(visible_root / "tidarr_nzo_missing")) in message


def test_retry_fetch_run_requeues_failed_tasks(tmp_db, monkeypatch):  # noqa: ARG001
    class _FailingDownloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            raise RuntimeError("submit failed")

    class _RecoveredDownloader:
        name = "tidarr"

        @staticmethod
        def submit(_artist: str, _album: str, _metadata: dict) -> str:
            return "tidarr_nzo_retry"

    build = _build_with_missing_album("Fetch Build Retry")
    monkeypatch.setattr("app.plugins.get_downloader", lambda: _FailingDownloader())
    run = fetch_pipeline.start_fetch_run(build["id"])
    failed_tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert failed_tasks[0]["stage"] == "failed"

    monkeypatch.setattr("app.plugins.get_downloader", lambda: _RecoveredDownloader())
    retried = fetch_pipeline.retry_fetch_run(run["id"])
    assert retried["retried"] == 1
    assert retried["submission"]["submitted"] == 1

    tasks = fetch_pipeline.list_fetch_tasks_for_run(run["id"])
    assert tasks[0]["stage"] == "submitted"
    assert tasks[0]["retry_count"] == 1
