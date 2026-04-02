from __future__ import annotations

from fastapi.responses import JSONResponse

from app.routes import acquisition
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

