"""Unit tests for NavidromeClient."""
import hashlib
from unittest.mock import patch, MagicMock
import pytest
import requests

from app.clients.navidrome_client import NavidromeClient, NavidromeError


@pytest.fixture
def client():
    return NavidromeClient("http://localhost:4533", "admin", "password")


def test_auth_params_token_mode(client):
    """Token auth: t=md5(password+salt), no plaintext password in params."""
    params = client._auth_params()
    assert "u" in params
    assert "t" in params
    assert "s" in params
    assert "v" in params
    assert "c" in params
    assert "p" not in params  # never send plaintext password
    # Verify token = md5(password + salt)
    salt = params["s"]
    expected_token = hashlib.md5(("password" + salt).encode()).hexdigest()
    assert params["t"] == expected_token


def test_ping_returns_true_on_ok(client):
    """ping() returns True when Subsonic returns status=ok."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "subsonic-response": {"status": "ok", "version": "1.16.1"}
    }
    with patch("app.clients.navidrome_client.requests.Session.get", return_value=mock_response):
        assert client.ping() is True


def test_ping_returns_false_on_failed(client):
    """ping() returns False when Subsonic returns status=failed."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "subsonic-response": {
            "status": "failed",
            "error": {"code": 40, "message": "Wrong username or password"}
        }
    }
    with patch("app.clients.navidrome_client.requests.Session.get", return_value=mock_response):
        assert client.ping() is False


def test_get_raises_navidrome_error_on_http_error(client):
    """_get() raises NavidromeError on non-2xx HTTP response."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    with patch("app.clients.navidrome_client.requests.Session.get", return_value=mock_response):
        with pytest.raises(NavidromeError):
            client._get("ping")


def test_get_raises_navidrome_error_on_subsonic_failed(client):
    """_get() raises NavidromeError when Subsonic response status=failed."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "subsonic-response": {
            "status": "failed",
            "error": {"code": 40, "message": "Wrong username or password"}
        }
    }
    with patch("app.clients.navidrome_client.requests.Session.get", return_value=mock_response):
        with pytest.raises(NavidromeError) as exc_info:
            client._get("ping")
    # Error message must NOT contain the password
    assert "password" not in str(exc_info.value).lower()


def test_get_artists_returns_list(client):
    """get_artists() returns a flat list of artist dicts."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "subsonic-response": {
            "status": "ok",
            "artists": {
                "index": [
                    {"name": "A", "artist": [
                        {"id": "ar-1", "name": "Aphex Twin", "albumCount": 10}
                    ]},
                    {"name": "R", "artist": [
                        {"id": "ar-2", "name": "Radiohead", "albumCount": 9}
                    ]},
                ]
            }
        }
    }
    with patch("app.clients.navidrome_client.requests.Session.get", return_value=mock_response):
        artists = client.get_artists()
    assert len(artists) == 2
    assert artists[0]["id"] == "ar-1"
    assert artists[1]["name"] == "Radiohead"
