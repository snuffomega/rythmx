from __future__ import annotations

import json

from fastapi.responses import JSONResponse

from app.routes import acquisition
from app.routes import forge
from app.routes import library_enrich
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
    calls = {"upsert": None, "status": None}

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

    result = forge.forge_builds_publish("build-1", {"name": "Published Build 1"})
    assert result["status"] == "ok"
    assert result["platform"] == "navidrome"
    assert result["platform_playlist_id"] == "platform-123"
    assert result["playlist"]["id"] == "build-1"
    assert calls["upsert"] == {
        "playlist_id": "build-1",
        "name": "Published Build 1",
        "track_ids": ["trk-1", "trk-2"],
    }
    assert calls["status"] == {"build_id": "build-1", "status": "published"}


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
