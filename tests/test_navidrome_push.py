"""Unit tests for navidrome_push."""
from unittest.mock import MagicMock
import pytest

from app.clients.navidrome_push import NavidromePusher


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def pusher(mock_client):
    return NavidromePusher(mock_client)


def test_push_playlist_creates_new_playlist(pusher, mock_client):
    """push_playlist calls create_playlist when no existing playlist found."""
    mock_client.get_playlists.return_value = []
    mock_client.create_playlist.return_value = {"id": "pl-1", "name": "My Mix"}

    result = pusher.push_playlist("My Mix", ["tr-1", "tr-2"])

    mock_client.create_playlist.assert_called_once_with("My Mix", ["tr-1", "tr-2"])
    assert result == "pl-1"


def test_push_playlist_updates_existing_playlist(pusher, mock_client):
    """push_playlist calls update_playlist when playlist already exists."""
    mock_client.get_playlists.return_value = [{"id": "pl-1", "name": "My Mix"}]

    pusher.push_playlist("My Mix", ["tr-3"])

    mock_client.update_playlist.assert_called_once_with("pl-1", ["tr-3"])
    mock_client.create_playlist.assert_not_called()


def test_push_playlist_returns_none_on_empty_tracks(pusher, mock_client):
    """push_playlist returns None and skips API calls for empty track list."""
    result = pusher.push_playlist("Empty Mix", [])
    mock_client.create_playlist.assert_not_called()
    assert result is None


def test_push_playlist_handles_client_error_gracefully(pusher, mock_client):
    """push_playlist returns None and logs on NavidromeError."""
    from app.clients.navidrome_client import NavidromeError
    mock_client.get_playlists.return_value = []
    mock_client.create_playlist.side_effect = NavidromeError("timeout")

    result = pusher.push_playlist("My Mix", ["tr-1"])
    assert result is None
