from __future__ import annotations

from collections import Counter

from app.main import app


def _route_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method in {"GET", "POST", "PUT", "PATCH"}:
                pairs.append((method, path))
    return pairs


def test_core_contract_routes_are_registered() -> None:
    pairs = set(_route_pairs())
    expected = {
        ("GET", "/api/v1/forge/new-music/config"),
        ("POST", "/api/v1/forge/new-music/config"),
        ("POST", "/api/v1/forge/new-music/run"),
        ("GET", "/api/v1/forge/new-music/results"),
        ("GET", "/api/v1/forge/discovery/config"),
        ("POST", "/api/v1/forge/discovery/config"),
        ("POST", "/api/v1/forge/discovery/run"),
        ("GET", "/api/v1/forge/discovery/results"),
        ("GET", "/api/v1/forge/builds"),
        ("POST", "/api/v1/forge/builds"),
        ("GET", "/api/v1/forge/builds/{build_id}"),
        ("POST", "/api/v1/forge/builds/{build_id}/publish"),
        ("GET", "/api/v1/library/artists"),
        ("GET", "/api/v1/library/releases"),
        ("GET", "/api/v1/library/albums"),
        ("GET", "/api/v1/library/tracks"),
        ("GET", "/api/v1/library/audit"),
        ("GET", "/api/v1/library/enrich/status"),
        ("POST", "/api/v1/library/enrich/full"),
        ("POST", "/api/v1/library/enrich/stop"),
        ("GET", "/api/v1/acquisition/queue"),
        ("POST", "/api/v1/acquisition/queue"),
        ("POST", "/api/v1/acquisition/check-now"),
        ("PATCH", "/api/v1/library/tracks/{track_id}/rating"),
        ("PUT", "/api/v1/library/releases/{release_id}/prefs"),
    }
    missing = expected - pairs
    assert not missing, f"Missing expected API contract routes: {sorted(missing)}"
    assert ("POST", "/api/v1/personal-discovery/run") not in pairs


def test_core_contract_routes_are_not_duplicated() -> None:
    counts = Counter(_route_pairs())
    keys = [
        ("GET", "/api/v1/forge/new-music/config"),
        ("POST", "/api/v1/forge/new-music/config"),
        ("POST", "/api/v1/forge/new-music/run"),
        ("GET", "/api/v1/forge/new-music/results"),
        ("GET", "/api/v1/forge/discovery/config"),
        ("POST", "/api/v1/forge/discovery/config"),
        ("POST", "/api/v1/forge/discovery/run"),
        ("GET", "/api/v1/forge/discovery/results"),
        ("GET", "/api/v1/forge/builds"),
        ("POST", "/api/v1/forge/builds"),
        ("GET", "/api/v1/forge/builds/{build_id}"),
        ("POST", "/api/v1/forge/builds/{build_id}/publish"),
        ("GET", "/api/v1/library/artists"),
        ("GET", "/api/v1/library/releases"),
        ("GET", "/api/v1/library/albums"),
        ("GET", "/api/v1/library/tracks"),
        ("GET", "/api/v1/library/audit"),
        ("GET", "/api/v1/library/enrich/status"),
        ("POST", "/api/v1/library/enrich/full"),
        ("POST", "/api/v1/library/enrich/stop"),
        ("POST", "/api/v1/acquisition/queue"),
        ("POST", "/api/v1/acquisition/check-now"),
    ]
    for key in keys:
        assert counts[key] == 1, f"Expected exactly one route registration for {key}"
