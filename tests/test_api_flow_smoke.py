from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.routes import acquisition
from app.routes import forge
from app.routes import library_enrich
from app.routes import library_playlists
from app.routes import library_stream
from app.routes.library import albums, artists, audit, releases, tracks


class _FakeCursor:
    def __init__(self, one=None, many=None, rowcount: int = 1):
        self._one = one
        self._many = many or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _FakeConn:
    def __init__(self, execute_fn):
        self._execute_fn = execute_fn

    def execute(self, sql, params=()):
        return self._execute_fn(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}


class _FakeNavidromeClient:
    def get_stream_url(self, song_id: str) -> str:
        return f"https://navidrome.local/stream/{song_id}"


class _FakeUpstreamResponse:
    def __init__(self, status_code: int = 200, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "audio/mpeg", "Content-Length": "4"}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        _ = chunk_size
        yield b"data"

    def close(self) -> None:
        return None


def test_acquisition_check_now_success(monkeypatch):
    called = {"check": False}

    def fake_check_queue():
        called["check"] = True

    monkeypatch.setattr("app.services.acquisition.check_queue", fake_check_queue)
    monkeypatch.setattr(
        acquisition.rythmx_store, "get_queue_stats", lambda: {"pending": 3, "total": 8}
    )

    result = acquisition.acquisition_check_now()
    assert result["status"] == "ok"
    assert result["pending"] == 3
    assert result["total"] == 8
    assert called["check"] is True


def test_acquisition_check_now_error(monkeypatch):
    def fake_check_queue():
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.acquisition.check_queue", fake_check_queue)

    result = acquisition.acquisition_check_now()
    assert isinstance(result, JSONResponse)
    assert result.status_code == 500


def test_library_enrich_full_accepts_positive_batch_size(monkeypatch):
    calls: list[int] = []

    class FakeOrch:
        def run_full(self, batch_size: int):
            calls.append(batch_size)

    monkeypatch.setattr(
        "app.services.api_orchestrator.EnrichmentOrchestrator.get",
        staticmethod(lambda: FakeOrch()),
    )

    result = library_enrich.enrich_full({"batch_size": 25})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 202
    assert calls == [25]


def test_library_enrich_full_rejects_invalid_batch_size():
    result = library_enrich.enrich_full({"batch_size": 0})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_library_enrich_stop_running_and_idle(monkeypatch):
    class FakeOrchIdle:
        def is_running(self):
            return False

    monkeypatch.setattr(
        "app.services.api_orchestrator.EnrichmentOrchestrator.get",
        staticmethod(lambda: FakeOrchIdle()),
    )
    idle_result = library_enrich.enrich_stop()
    assert idle_result["status"] == "ok"
    assert "No enrichment running" in idle_result["message"]

    state = {"stopped": False}

    class FakeOrchRunning:
        def is_running(self):
            return True

        def stop(self):
            state["stopped"] = True

    monkeypatch.setattr(
        "app.services.api_orchestrator.EnrichmentOrchestrator.get",
        staticmethod(lambda: FakeOrchRunning()),
    )
    running_result = library_enrich.enrich_stop()
    assert running_result["status"] == "ok"
    assert "Stop signal sent" in running_result["message"]
    assert state["stopped"] is True


def test_library_enrich_status_smoke(monkeypatch):
    class FakeOrch:
        _started_at = "2026-04-02T12:00:00Z"

        def is_running(self):
            return True

    monkeypatch.setattr(
        "app.services.api_orchestrator.EnrichmentOrchestrator.get",
        staticmethod(lambda: FakeOrch()),
    )
    monkeypatch.setattr("app.db.rythmx_store.get_setting", lambda _k: "stage2")

    def fake_execute(_sql, _params=()):
        rows = [
            {"source": "itunes", "status": "found", "cnt": 3},
            {"source": "itunes", "status": "not_found", "cnt": 1},
            {"source": "deezer", "status": "error", "cnt": 2},
        ]
        return _FakeCursor(many=rows)

    monkeypatch.setattr("app.db.rythmx_store._connect", lambda: _FakeConn(fake_execute))

    result = library_enrich.enrich_status()
    assert result["status"] == "ok"
    assert result["running"] is True
    assert result["phase"] == "stage2"
    assert result["workers"]["itunes"]["found"] == 3
    assert result["workers"]["itunes"]["not_found"] == 1
    assert result["workers"]["deezer"]["errors"] == 2


def test_split_library_module_validation_guards():
    # artists module
    artist_cover = artists.library_artist_set_cover("a1", {"cover_url": "not-a-url"})
    assert isinstance(artist_cover, JSONResponse)
    assert artist_cover.status_code == 400

    # tracks module
    bad_rating = tracks.library_rate_track("t1", {"rating": 99})
    assert isinstance(bad_rating, JSONResponse)
    assert bad_rating.status_code == 400

    # audit module
    bad_confirm = audit.library_audit_confirm({"entity_type": "album"})
    assert isinstance(bad_confirm, JSONResponse)
    assert bad_confirm.status_code == 400

    bad_reject = audit.library_audit_reject({"entity_type": "album"})
    assert isinstance(bad_reject, JSONResponse)
    assert bad_reject.status_code == 400


def test_releases_prefs_returns_404_for_missing_release(monkeypatch):
    def fake_execute(_sql, _params=()):
        # The first query is "SELECT 1 FROM lib_releases ..." -> not found
        return _FakeCursor(one=None)

    monkeypatch.setattr(
        releases.rythmx_store, "_connect", lambda: _FakeConn(fake_execute)
    )

    result = releases.library_update_release_prefs("missing-id", {"dismissed": True})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


def test_album_detail_returns_404_for_missing_album(monkeypatch):
    def fake_execute(_sql, _params=()):
        return _FakeCursor(one=None)

    monkeypatch.setattr(
        albums.rythmx_store, "_connect", lambda: _FakeConn(fake_execute)
    )

    result = albums.library_album_detail("missing-id")
    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


def test_forge_discovery_config_validation_rejects_invalid_values():
    result = forge.discovery_save_config({"closeness": 0})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert isinstance(body["message"], str)
    assert body["code"] == "FORGE_VALIDATION_ERROR"


def test_forge_discovery_run_and_results_contract(monkeypatch):
    expected = [
        {
            "artist": "Example Artist",
            "image": None,
            "reason": "From Forge neighborhood cache",
            "similarity": None,
            "tags": [],
        }
    ]

    monkeypatch.setattr(
        "app.services.forge.discovery_runner.run_discovery_pipeline",
        lambda _override=None: {"artists_found": 1, "artists": expected},
    )
    monkeypatch.setattr(
        "app.services.forge.discovery_runner.get_results",
        lambda: expected,
    )

    run_result = forge.discovery_run({"run_mode": "build", "max_tracks": 25})
    assert run_result["status"] == "ok"
    assert run_result["artists_found"] == 1
    assert run_result["artists"] == expected

    results_result = forge.discovery_get_results()
    assert results_result["status"] == "ok"
    assert results_result["artists"] == expected


def test_forge_new_music_validation_rejects_invalid_values():
    config_result = forge.nm_save_config({"nm_period": "2weeks"})
    assert isinstance(config_result, JSONResponse)
    assert config_result.status_code == 400
    config_body = json.loads(config_result.body.decode("utf-8"))
    assert config_body["status"] == "error"
    assert isinstance(config_body["message"], str)
    assert config_body["code"] == "FORGE_VALIDATION_ERROR"

    run_result = forge.nm_run({"nm_lookback_days": 0})
    assert isinstance(run_result, JSONResponse)
    assert run_result.status_code == 400
    run_body = json.loads(run_result.body.decode("utf-8"))
    assert run_body["status"] == "error"
    assert isinstance(run_body["message"], str)
    assert run_body["code"] == "FORGE_VALIDATION_ERROR"


def test_forge_new_music_run_contract(monkeypatch):
    expected_releases = [
        {
            "id": "r1",
            "artist_deezer_id": "123",
            "artist_name": "Example Artist",
            "title": "Example Release",
            "record_type": "album",
            "release_date": "2026-03-01",
            "cover_url": None,
            "in_library": 0,
        }
    ]

    monkeypatch.setattr(
        "app.services.forge.new_music_runner.run_new_music_pipeline",
        lambda _override=None: {"artists_checked": 12, "neighbors_found": 8, "releases_found": 1},
    )
    monkeypatch.setattr("app.routes.forge._get_discovered_releases", lambda: expected_releases)

    result = forge.nm_run({"nm_period": "1month", "nm_lookback_days": 30})
    assert result["status"] == "ok"
    assert result["artists_checked"] == 12
    assert result["neighbors_found"] == 8
    assert result["releases_found"] == 1
    assert result["releases"] == expected_releases


def test_forge_discovery_runtime_error_contract(monkeypatch):
    def _raise(_override=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.forge.discovery_runner.run_discovery_pipeline", _raise)

    result = forge.discovery_run({"run_mode": "build"})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 500
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["message"] == "boom"
    assert body["code"] == "FORGE_DISCOVERY_FAILED"


def test_forge_new_music_runtime_error_contract(monkeypatch):
    def _raise(_override=None):
        raise RuntimeError("nm failed")

    monkeypatch.setattr("app.services.forge.new_music_runner.run_new_music_pipeline", _raise)

    result = forge.nm_run({"nm_period": "1month"})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 500
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["message"] == "nm failed"
    assert body["code"] == "FORGE_RUN_FAILED"


def test_forge_builds_validation_rejects_invalid_source():
    result = forge.forge_builds_create({"source": "bad_source", "track_list": []})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["code"] == "FORGE_VALIDATION_ERROR"


def test_forge_builds_create_and_list_contract(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "New Music Build",
        "source": "new_music",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"title": "Track A"}],
        "summary": {"releases_found": 1},
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }

    monkeypatch.setattr(
        forge.rythmx_store,
        "create_forge_build",
        lambda **kwargs: fake_build,
    )
    monkeypatch.setattr(
        forge.rythmx_store,
        "list_forge_builds",
        lambda source=None, limit=100: [fake_build],
    )

    created = forge.forge_builds_create(
        {
            "name": "New Music Build",
            "source": "new_music",
            "status": "ready",
            "run_mode": "build",
            "track_list": [{"title": "Track A"}],
            "summary": {"releases_found": 1},
        }
    )
    assert created["status"] == "ok"
    assert created["build"]["id"] == "build-1"
    assert created["build"]["item_count"] == 1

    listed = forge.forge_builds_list(source=None, limit=25)
    assert listed["status"] == "ok"
    assert len(listed["builds"]) == 1
    assert listed["builds"][0]["id"] == "build-1"


def test_forge_build_get_and_delete_contract(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": None,
        "track_list": [],
        "summary": {},
        "item_count": 0,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }

    monkeypatch.setattr(
        forge.rythmx_store,
        "get_forge_build",
        lambda build_id: fake_build if build_id == "build-1" else None,
    )
    monkeypatch.setattr(
        forge.rythmx_store,
        "delete_forge_build",
        lambda build_id: build_id == "build-1",
    )

    found = forge.forge_builds_get("build-1")
    assert found["status"] == "ok"
    assert found["build"]["name"] == "Build 1"

    missing_get = forge.forge_builds_get("missing")
    assert isinstance(missing_get, JSONResponse)
    assert missing_get.status_code == 404
    get_body = json.loads(missing_get.body.decode("utf-8"))
    assert get_body["code"] == "FORGE_BUILD_NOT_FOUND"

    deleted = forge.forge_builds_delete("build-1")
    assert deleted["status"] == "ok"
    assert deleted["deleted"] is True

    missing_delete = forge.forge_builds_delete("missing")
    assert isinstance(missing_delete, JSONResponse)
    assert missing_delete.status_code == 404
    del_body = json.loads(missing_delete.body.decode("utf-8"))
    assert del_body["code"] == "FORGE_BUILD_NOT_FOUND"


def test_forge_build_update_contract(monkeypatch):
    fake_updated = {
        "id": "build-1",
        "name": "Renamed Build",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}],
        "summary": {"note": "updated"},
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T21:00:00",
    }
    calls = {"payload": None}

    monkeypatch.setattr(
        forge.rythmx_store,
        "update_forge_build",
        lambda build_id, **kwargs: calls.__setitem__("payload", {"build_id": build_id, **kwargs}) or fake_updated,
    )

    result = forge.forge_builds_update(
        "build-1",
        {
            "name": "Renamed Build",
            "status": "ready",
            "run_mode": "build",
            "track_list": [{"track_id": "trk-1"}],
            "summary": {"note": "updated"},
        },
    )
    assert result["status"] == "ok"
    assert result["build"]["name"] == "Renamed Build"
    assert calls["payload"]["build_id"] == "build-1"
    assert calls["payload"]["name"] == "Renamed Build"


def test_forge_build_update_validation_and_missing(monkeypatch):
    bad = forge.forge_builds_update("build-1", {"status": "invalid"})
    assert isinstance(bad, JSONResponse)
    assert bad.status_code == 400
    bad_body = json.loads(bad.body.decode("utf-8"))
    assert bad_body["code"] == "FORGE_VALIDATION_ERROR"

    monkeypatch.setattr(
        forge.rythmx_store,
        "update_forge_build",
        lambda build_id, **kwargs: None,
    )
    missing = forge.forge_builds_update("missing", {"name": "x"})
    assert isinstance(missing, JSONResponse)
    assert missing.status_code == 404
    missing_body = json.loads(missing.body.decode("utf-8"))
    assert missing_body["code"] == "FORGE_BUILD_NOT_FOUND"


def test_forge_build_publish_contract(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}, {"track_id": "trk-2"}, {"track_id": "trk-1"}],
        "summary": {},
        "item_count": 3,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }
    calls = {"upsert": None, "status": None, "library_cache": None}

    class _FakePusher:
        @staticmethod
        def push_playlist(_name, _track_ids):
            return "platform-123"

    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda build_id: fake_build)
    monkeypatch.setattr(forge, "_get_library_platform", lambda: "navidrome")
    monkeypatch.setattr(forge, "get_playlist_pusher", lambda: _FakePusher())
    monkeypatch.setattr(
        forge.rythmx_store,
        "upsert_forge_playlist",
        lambda playlist_id, name, track_ids, pushed_at=None: calls.__setitem__(
            "upsert",
            {"playlist_id": playlist_id, "name": name, "track_ids": track_ids},
        )
        or {"id": playlist_id, "name": name, "track_count": len(track_ids)},
    )
    monkeypatch.setattr(
        forge.rythmx_store,
        "update_forge_build_status",
        lambda build_id, status: calls.__setitem__("status", {"build_id": build_id, "status": status}) or True,
    )
    monkeypatch.setattr(
        forge,
        "_sync_library_playlist_cache",
        lambda **kwargs: calls.__setitem__("library_cache", kwargs)
        or {
            "id": "platform-123",
            "name": kwargs["playlist_name"],
            "source_platform": kwargs["platform"],
            "track_count": 2,
            "duration_ms": 300000,
        },
    )

    result = forge.forge_builds_publish("build-1", {"name": "Published Build 1"})
    assert result["status"] == "ok"
    assert result["platform"] == "navidrome"
    assert result["platform_playlist_id"] == "platform-123"
    assert result["playlist"]["id"] == "build-1"
    assert result["library_playlist_cached"] is True
    assert result["library_playlist"]["id"] == "platform-123"
    assert calls["upsert"] == {
        "playlist_id": "build-1",
        "name": "Published Build 1",
        "track_ids": ["trk-1", "trk-2"],
    }
    assert calls["status"] == {"build_id": "build-1", "status": "published"}
    assert calls["library_cache"] == {
        "playlist_id": "platform-123",
        "playlist_name": "Published Build 1",
        "platform": "navidrome",
        "track_ids": ["trk-1", "trk-2"],
    }


def test_forge_publish_flow_visible_in_library_playlists(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE lib_artists (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE lib_albums (id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE lib_tracks (
            id TEXT PRIMARY KEY,
            title TEXT,
            artist_id TEXT,
            album_id TEXT,
            duration INTEGER,
            file_path TEXT
        );
        CREATE TABLE lib_playlists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_platform TEXT NOT NULL,
            cover_url TEXT,
            track_count INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            synced_at TEXT
        );
        CREATE TABLE lib_playlist_tracks (
            playlist_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (playlist_id, track_id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO lib_artists (id, name) VALUES (?, ?)",
        [("a1", "Artist 1"), ("a2", "Artist 2")],
    )
    conn.executemany(
        "INSERT INTO lib_albums (id, title) VALUES (?, ?)",
        [("al1", "Album 1"), ("al2", "Album 2")],
    )
    conn.executemany(
        "INSERT INTO lib_tracks (id, title, artist_id, album_id, duration, file_path) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("trk-1", "Track One", "a1", "al1", 120000, None),
            ("trk-2", "Track Two", "a2", "al2", 180000, None),
        ],
    )

    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}, {"track_id": "trk-2"}],
        "summary": {},
        "item_count": 2,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }

    class _FakePusher:
        @staticmethod
        def push_playlist(_name, _track_ids):
            return "pl-123"

    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda _build_id: fake_build)
    monkeypatch.setattr(forge, "_get_library_platform", lambda: "navidrome")
    monkeypatch.setattr(forge, "get_playlist_pusher", lambda: _FakePusher())
    monkeypatch.setattr(forge.rythmx_store, "upsert_forge_playlist", lambda **_kwargs: {"id": "build-1"})
    monkeypatch.setattr(forge.rythmx_store, "update_forge_build_status", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(forge.rythmx_store, "_connect", lambda: conn)
    monkeypatch.setattr(library_playlists.rythmx_store, "_connect", lambda: conn)

    publish_result = forge.forge_builds_publish("build-1", {"name": "Published Build"})
    assert publish_result["status"] == "ok"
    assert publish_result["platform_playlist_id"] == "pl-123"
    assert publish_result["library_playlist_cached"] is True
    assert publish_result["library_playlist"]["id"] == "pl-123"
    assert publish_result["library_playlist"]["track_count"] == 2

    listed = library_playlists.list_playlists()
    assert listed["status"] == "ok"
    assert len(listed["playlists"]) == 1
    assert listed["playlists"][0]["id"] == "pl-123"
    assert listed["playlists"][0]["name"] == "Published Build"

    track_rows = library_playlists.get_playlist_tracks("pl-123")
    assert track_rows["status"] == "ok"
    assert [t["track_id"] for t in track_rows["tracks"]] == ["trk-1", "trk-2"]


def test_library_playlists_add_tracks_contract(monkeypatch):
    monkeypatch.setattr(
        "app.services.library_playlists_service.add_tracks_to_playlist",
        lambda playlist_id, track_ids: {
            "playlist_id": playlist_id,
            "added_count": len(track_ids),
            "track_count": 12,
        },
    )

    result = library_playlists.add_playlist_tracks(
        "pl-1",
        library_playlists.AddTracksBody(track_ids=["t1", "t2"]),
    )
    assert result["status"] == "ok"
    assert result["playlist_id"] == "pl-1"
    assert result["added_count"] == 2
    assert result["track_count"] == 12


def test_library_playlists_add_tracks_validation_and_not_found(monkeypatch):
    with pytest.raises(HTTPException) as empty_exc:
        library_playlists.add_playlist_tracks(
            "pl-1",
            library_playlists.AddTracksBody(track_ids=[]),
        )
    assert empty_exc.value.status_code == 400

    def _not_found(_playlist_id, _track_ids):
        raise ValueError("Playlist not found: pl-1")

    monkeypatch.setattr(
        "app.services.library_playlists_service.add_tracks_to_playlist",
        _not_found,
    )

    with pytest.raises(HTTPException) as not_found_exc:
        library_playlists.add_playlist_tracks(
            "pl-1",
            library_playlists.AddTracksBody(track_ids=["t1"]),
        )
    assert not_found_exc.value.status_code == 404


def test_forge_build_publish_jellyfin_stub(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}],
        "summary": {},
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }
    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda build_id: fake_build)
    monkeypatch.setattr(forge, "_get_library_platform", lambda: "jellyfin")

    result = forge.forge_builds_publish("build-1", None)
    assert isinstance(result, JSONResponse)
    assert result.status_code == 501
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["code"] == "FORGE_PUBLISH_NOT_IMPLEMENTED"


def test_forge_build_fetch_contract(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}],
        "summary": {},
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }

    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda build_id: fake_build if build_id == "build-1" else None)
    monkeypatch.setattr(forge.rythmx_store, "get_setting", lambda key, default=None: "true")

    result = forge.forge_builds_fetch("build-1")
    assert isinstance(result, JSONResponse)
    assert result.status_code == 501
    body = json.loads(result.body.decode("utf-8"))
    assert body["code"] == "FORGE_FETCH_NOT_IMPLEMENTED"


def test_forge_build_fetch_disabled_and_missing(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Build 1",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}],
        "summary": {},
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }
    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda build_id: fake_build if build_id == "build-1" else None)
    monkeypatch.setattr(forge.rythmx_store, "get_setting", lambda key, default=None: "false")

    disabled = forge.forge_builds_fetch("build-1")
    assert isinstance(disabled, JSONResponse)
    assert disabled.status_code == 400
    disabled_body = json.loads(disabled.body.decode("utf-8"))
    assert disabled_body["code"] == "FORGE_FETCH_DISABLED"

    missing = forge.forge_builds_fetch("missing")
    assert isinstance(missing, JSONResponse)
    assert missing.status_code == 404
    missing_body = json.loads(missing.body.decode("utf-8"))
    assert missing_body["code"] == "FORGE_BUILD_NOT_FOUND"


def test_forge_build_resync_contract(monkeypatch):
    fake_build = {
        "id": "build-1",
        "name": "Sync Build",
        "source": "sync",
        "status": "ready",
        "run_mode": "build",
        "track_list": [{"track_id": "trk-1"}],
        "summary": {
            "source": "spotify",
            "source_url": "https://open.spotify.com/playlist/abc",
        },
        "item_count": 1,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }
    fake_import = {
        "status": "ok",
        "track_count": 2,
        "owned_count": 1,
        "tracks": [
            {
                "track_name": "Track A",
                "artist_name": "Artist A",
                "album_name": "Album A",
                "spotify_track_id": "sp-a",
                "is_owned": True,
                "plex_rating_key": "trk-a",
            },
            {
                "track_name": "Track B",
                "artist_name": "Artist B",
                "album_name": "Album B",
                "spotify_track_id": "sp-b",
                "is_owned": False,
                "plex_rating_key": None,
            },
        ],
    }
    fake_updated = {**fake_build, "item_count": 2}
    calls = {"update": None}

    monkeypatch.setattr(forge.rythmx_store, "get_forge_build", lambda build_id: fake_build if build_id == "build-1" else None)
    monkeypatch.setattr(forge, "_import_sync_source", lambda source, source_url: fake_import)
    monkeypatch.setattr(
        forge.rythmx_store,
        "update_forge_build",
        lambda build_id, **kwargs: calls.__setitem__("update", {"build_id": build_id, **kwargs}) or fake_updated,
    )

    result = forge.forge_builds_resync("build-1")
    assert result["status"] == "ok"
    assert result["source"] == "spotify"
    assert result["track_count"] == 2
    assert result["owned_count"] == 1
    assert result["missing_count"] == 1
    assert calls["update"] is not None
    assert calls["update"]["build_id"] == "build-1"
    assert calls["update"]["status"] == "ready"
    assert calls["update"]["run_mode"] == "build"
    assert len(calls["update"]["track_list"]) == 2


def test_forge_build_resync_validation_and_missing(monkeypatch):
    not_sync = {
        "id": "build-2",
        "name": "Manual Build",
        "source": "manual",
        "status": "ready",
        "run_mode": "build",
        "track_list": [],
        "summary": {},
        "item_count": 0,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }
    missing_url = {
        "id": "build-3",
        "name": "Sync Build Missing URL",
        "source": "sync",
        "status": "ready",
        "run_mode": "build",
        "track_list": [],
        "summary": {"source": "spotify"},
        "item_count": 0,
        "created_at": "2026-04-02T20:00:00",
        "updated_at": "2026-04-02T20:00:00",
    }

    monkeypatch.setattr(
        forge.rythmx_store,
        "get_forge_build",
        lambda build_id: (
            not_sync if build_id == "build-2" else
            missing_url if build_id == "build-3" else
            None
        ),
    )

    invalid_source = forge.forge_builds_resync("build-2")
    assert isinstance(invalid_source, JSONResponse)
    assert invalid_source.status_code == 400
    invalid_body = json.loads(invalid_source.body.decode("utf-8"))
    assert invalid_body["code"] == "FORGE_SYNC_RESYNC_INVALID_SOURCE"

    missing_source_url = forge.forge_builds_resync("build-3")
    assert isinstance(missing_source_url, JSONResponse)
    assert missing_source_url.status_code == 400
    missing_body = json.loads(missing_source_url.body.decode("utf-8"))
    assert missing_body["code"] == "FORGE_SYNC_RESYNC_MISSING_URL"

    missing = forge.forge_builds_resync("missing")
    assert isinstance(missing, JSONResponse)
    assert missing.status_code == 404
    missing_lookup_body = json.loads(missing.body.decode("utf-8"))
    assert missing_lookup_body["code"] == "FORGE_BUILD_NOT_FOUND"


def test_forge_sync_load_validation_requires_source_url():
    result = forge.forge_sync_load({})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["code"] == "FORGE_VALIDATION_ERROR"


def test_forge_sync_load_rejects_unknown_source():
    result = forge.forge_sync_load({"source_url": "https://example.com/playlist/123"})
    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
    body = json.loads(result.body.decode("utf-8"))
    assert body["status"] == "error"
    assert body["code"] == "FORGE_SYNC_UNSUPPORTED_SOURCE"


def test_forge_sync_load_create_build_contract(monkeypatch):
    fake_import = {
        "status": "ok",
        "name": "Imported Playlist",
        "track_count": 2,
        "owned_count": 1,
        "tracks": [
            {
                "track_name": "Track A",
                "artist_name": "Artist A",
                "album_name": "Album A",
                "spotify_track_id": "sp-a",
                "is_owned": True,
                "plex_rating_key": "trk-a",
            },
            {
                "track_name": "Track B",
                "artist_name": "Artist B",
                "album_name": "Album B",
                "spotify_track_id": "",
                "is_owned": False,
                "plex_rating_key": None,
            },
        ],
    }
    calls = {"create": None}
    fake_build = {"id": "sync-build-1", "source": "sync", "status": "ready"}

    monkeypatch.setattr(forge, "_import_sync_source", lambda source, source_url: fake_import)
    monkeypatch.setattr(
        forge.rythmx_store,
        "create_forge_build",
        lambda **kwargs: calls.__setitem__("create", kwargs) or fake_build,
    )

    result = forge.forge_sync_load({"source_url": "https://open.spotify.com/playlist/abc"})
    assert result["status"] == "ok"
    assert result["source"] == "spotify"
    assert result["track_count"] == 2
    assert result["owned_count"] == 1
    assert result["missing_count"] == 1
    assert result["queue_build"] is True
    assert result["build"] == fake_build
    assert len(result["tracks"]) == 2
    assert result["tracks"][0]["track_id"] == "trk-a"
    assert calls["create"] is not None
    assert calls["create"]["source"] == "sync"
    assert calls["create"]["run_mode"] == "build"


def test_forge_sync_load_no_queue_build(monkeypatch):
    fake_import = {
        "status": "ok",
        "name": "Imported Playlist",
        "track_count": 1,
        "owned_count": 0,
        "tracks": [
            {
                "track_name": "Track A",
                "artist_name": "Artist A",
                "album_name": "Album A",
                "spotify_track_id": "sp-a",
                "is_owned": False,
                "plex_rating_key": None,
            },
        ],
    }
    calls = {"create_called": False}

    monkeypatch.setattr(forge, "_import_sync_source", lambda source, source_url: fake_import)
    monkeypatch.setattr(
        forge.rythmx_store,
        "create_forge_build",
        lambda **kwargs: calls.__setitem__("create_called", True) or {"id": "unused"},
    )

    result = forge.forge_sync_load(
        {"source_url": "https://www.last.fm/user/test/playlists/123", "queue_build": False}
    )
    assert result["status"] == "ok"
    assert result["source"] == "lastfm"
    assert result["queue_build"] is False
    assert result["build"] is None
    assert calls["create_called"] is False


def test_library_stream_navidrome_forwards_range_and_returns_partial(monkeypatch):
    captured: dict[str, object] = {"url": None, "headers": None}

    def _fake_get(url, headers=None, stream=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        assert stream is True
        assert timeout == 30
        return _FakeUpstreamResponse(
            status_code=206,
            headers={
                "Content-Type": "audio/flac",
                "Content-Length": "4",
                "Content-Range": "bytes 0-3/1000",
            },
        )

    monkeypatch.setattr(library_stream, "_verify_key", lambda _k: None)
    monkeypatch.setattr(
        library_stream,
        "_get_track",
        lambda _track_id: {"id": "tr-1", "source_platform": "navidrome", "file_path": None},
    )
    monkeypatch.setattr("app.db.navidrome_reader._get_client", lambda: _FakeNavidromeClient())
    monkeypatch.setattr("requests.get", _fake_get)

    response = library_stream.stream_track(
        "tr-1",
        _FakeRequest(headers={"range": "bytes=0-3"}),
        api_key="test",
    )

    assert response.status_code == 206
    assert response.media_type == "audio/flac"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 0-3/1000"
    assert response.headers["content-length"] == "4"
    assert captured["url"] == "https://navidrome.local/stream/tr-1"
    assert captured["headers"] == {"Range": "bytes=0-3"}


def test_library_stream_plex_returns_redirect(monkeypatch):
    class _FakePlexTrack:
        @staticmethod
        def getStreamURL() -> str:
            return "https://plex.local/stream/track.mp3"

    class _FakePlexServer:
        def __init__(self, url: str, token: str):
            assert url == "http://plex.example"
            assert token == "plex-token"

        @staticmethod
        def fetchItem(track_id: int):
            assert track_id == 123
            return _FakePlexTrack()

    monkeypatch.setattr(library_stream, "_verify_key", lambda _k: None)
    monkeypatch.setattr(
        library_stream,
        "_get_track",
        lambda _track_id: {"id": "123", "source_platform": "plex", "file_path": None},
    )
    monkeypatch.setattr("app.config.PLEX_URL", "http://plex.example")
    monkeypatch.setattr("app.config.PLEX_TOKEN", "plex-token")
    monkeypatch.setattr("plexapi.server.PlexServer", _FakePlexServer)

    response = library_stream.stream_track("123", _FakeRequest(), api_key="test")
    assert response.status_code == 302
    assert response.headers["location"] == "https://plex.local/stream/track.mp3"
