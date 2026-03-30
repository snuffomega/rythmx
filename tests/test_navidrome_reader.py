"""Unit tests for navidrome_reader."""
import sqlite3
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Create a minimal in-memory-style DB at a temp path with lib_* tables."""
    db_path = str(tmp_path / "test_rythmx.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE lib_artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            source_platform TEXT DEFAULT 'navidrome',
            added_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            removed_at TEXT,
            musicbrainz_id TEXT,
            thumb_url_navidrome TEXT,
            genres_json_navidrome TEXT,
            spotify_artist_id TEXT,
            deezer_artist_id TEXT,
            itunes_artist_id TEXT
        );

        CREATE TABLE lib_albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            local_title TEXT,
            title_lower TEXT NOT NULL,
            year INTEGER,
            source_platform TEXT DEFAULT 'navidrome',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            removed_at TEXT,
            musicbrainz_id TEXT,
            thumb_url_navidrome TEXT,
            genres_json_navidrome TEXT
        );

        CREATE TABLE lib_tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            title_lower TEXT NOT NULL,
            track_number INTEGER,
            disc_number INTEGER,
            duration INTEGER,
            file_path TEXT,
            file_size INTEGER,
            rating REAL,
            play_count INTEGER,
            skip_count INTEGER,
            added_at TEXT,
            source_platform TEXT DEFAULT 'navidrome',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            removed_at TEXT,
            sample_rate INTEGER,
            bit_depth INTEGER,
            channel_count INTEGER,
            replay_gain_track REAL,
            replay_gain_album REAL,
            replay_gain_track_peak REAL,
            replay_gain_album_peak REAL,
            tempo_navidrome REAL,
            musicbrainz_id TEXT
        );

        CREATE TABLE lib_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _make_mock_client():
    """Return a mock NavidromeClient with one artist, one album, one track."""
    client = MagicMock()
    client.get_artists.return_value = [
        {"id": "ar-1", "name": "Radiohead", "coverArt": "ar-ar-1",
         "musicBrainzId": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
         "genres": [{"name": "Alternative Rock"}, {"name": "Post-Rock"}]}
    ]
    client.get_artist.return_value = {
        "id": "ar-1", "name": "Radiohead",
        "album": [{"id": "al-1", "name": "OK Computer", "year": 1997}]
    }
    client.get_album.return_value = {
        "id": "al-1", "name": "OK Computer", "year": 1997,
        "coverArt": "al-al-1",
        "musicBrainzId": "mbid-album-1",
        "genres": [{"name": "Alternative Rock"}],
        "song": [{
            "id": "tr-1", "title": "Paranoid Android",
            "track": 3, "discNumber": 1, "duration": 383,
            "path": "/music/radiohead/ok_computer/03_paranoid_android.flac",
            "size": 24000000,
            "userRating": 4,  # 0-5 scale from Navidrome
            "playCount": 42,
            "musicBrainzId": "mbid-track-1",
            "bpm": 84,
            "samplingRate": 44100,
            "bitDepth": 16,
            "channelCount": 2,
            "replayGain": {
                "trackGain": -3.5,
                "albumGain": -4.1,
                "trackPeak": 0.98,
                "albumPeak": 0.99,
            }
        }]
    }
    return client


def test_sync_library_populates_tables(tmp_db):
    """sync_library() creates rows in lib_artists, lib_albums, lib_tracks."""
    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        result = reader.sync_library()

    assert result["artist_count"] == 1
    assert result["album_count"] == 1
    assert result["track_count"] == 1

    conn = sqlite3.connect(tmp_db)
    artist = conn.execute("SELECT * FROM lib_artists WHERE id='ar-1'").fetchone()
    album = conn.execute("SELECT * FROM lib_albums WHERE id='al-1'").fetchone()
    track = conn.execute("SELECT * FROM lib_tracks WHERE id='tr-1'").fetchone()
    conn.close()

    assert artist is not None
    assert album is not None
    assert track is not None


def test_sync_library_normalizes_rating(tmp_db):
    """Navidrome 0-5 rating is stored as 0-10 (multiplied by 2)."""
    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        reader.sync_library()

    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT rating FROM lib_tracks WHERE id='tr-1'").fetchone()
    conn.close()
    # userRating=4 from Navidrome (0-5) → stored as 8 (0-10)
    assert row[0] == 8.0


def test_sync_library_writes_audio_quality(tmp_db):
    """Audio quality fields from OpenSubsonic are written to the new columns."""
    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        reader.sync_library()

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM lib_tracks WHERE id='tr-1'").fetchone()
    conn.close()

    assert row["sample_rate"] == 44100
    assert row["bit_depth"] == 16
    assert row["channel_count"] == 2
    assert row["replay_gain_track"] == -3.5
    assert row["replay_gain_album"] == -4.1
    assert row["tempo_navidrome"] == 84.0


def test_sync_library_writes_musicbrainz_id(tmp_db):
    """MusicBrainzId from Navidrome file tags is written directly during Stage 1."""
    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        reader.sync_library()

    conn = sqlite3.connect(tmp_db)
    artist = conn.execute("SELECT musicbrainz_id FROM lib_artists WHERE id='ar-1'").fetchone()
    conn.close()
    assert artist[0] == "a74b1b7f-71a5-4011-9441-d0b5e4122711"


def test_sync_library_tombstones_removed_artists(tmp_db):
    """Artists present in DB but absent from this sync get removed_at set."""
    # Pre-populate with an artist not in the mock response
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO lib_artists (id, name, name_lower, source_platform) "
        "VALUES ('ar-old', 'Old Artist', 'old artist', 'navidrome')"
    )
    conn.commit()
    conn.close()

    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        reader.sync_library()

    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT removed_at FROM lib_artists WHERE id='ar-old'").fetchone()
    conn.close()
    assert row[0] is not None  # tombstoned


def test_sync_library_idempotent_preserves_enrichment(tmp_db):
    """Re-sync does not overwrite enrichment-only columns written by Stage 2/3."""
    # Pre-populate with enrichment data (as if Stage 2/3 had already run)
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO lib_artists (id, name, name_lower, source_platform, "
        "musicbrainz_id, genres_json_navidrome) "
        "VALUES ('ar-1', 'Radiohead', 'radiohead', 'navidrome', "
        "'a74b1b7f-71a5-4011-9441-d0b5e4122711', '[\"Alternative Rock\"]')"
    )
    conn.commit()
    conn.close()

    # Sync with a client that returns NO musicBrainzId and NO genres for the artist
    mock_client = MagicMock()
    mock_client.get_artists.return_value = [
        {"id": "ar-1", "name": "Radiohead"}  # no musicBrainzId, no genres
    ]
    mock_client.get_artist.return_value = {"id": "ar-1", "name": "Radiohead", "album": []}

    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db

        import app.db.navidrome_reader as reader
        reader.sync_library()

    # Enrichment data must be preserved — COALESCE guards prevent NULL overwrite
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT musicbrainz_id, genres_json_navidrome FROM lib_artists WHERE id='ar-1'"
    ).fetchone()
    conn.close()
    assert row[0] == "a74b1b7f-71a5-4011-9441-d0b5e4122711"  # preserved
    assert row[1] == '["Alternative Rock"]'  # preserved


def test_is_db_accessible_false_when_empty(tmp_db):
    """is_db_accessible returns False when no navidrome tracks in DB."""
    with patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        import app.db.navidrome_reader as reader
        # No sync run yet — DB is empty
        assert reader.is_db_accessible() is False


def test_get_track_count_after_sync(tmp_db):
    """get_track_count returns correct count after sync."""
    mock_client = _make_mock_client()
    with patch("app.db.navidrome_reader._get_client", return_value=mock_client), \
         patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        mock_config.NAVIDROME_URL = "http://localhost:4533"
        mock_config.NAVIDROME_USER = "admin"
        mock_config.NAVIDROME_PASS = "password"

        import app.db.navidrome_reader as reader
        reader.sync_library()

    with patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        assert reader.get_track_count() == 1


def test_get_native_artist_id(tmp_db):
    """get_native_artist_id returns ID for case-insensitive name match."""
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO lib_artists (id, name, name_lower, source_platform) "
        "VALUES ('ar-1', 'Radiohead', 'radiohead', 'navidrome')"
    )
    conn.commit()
    conn.close()

    with patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        import app.db.navidrome_reader as reader
        assert reader.get_native_artist_id("Radiohead") == "ar-1"
        assert reader.get_native_artist_id("radiohead") == "ar-1"
        assert reader.get_native_artist_id("Unknown") is None


def test_check_album_owned_returns_id_on_match(tmp_db):
    """check_album_owned returns album id when artist + title match."""
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO lib_artists (id, name, name_lower, source_platform) "
        "VALUES ('ar-1', 'Radiohead', 'radiohead', 'navidrome')"
    )
    conn.execute(
        "INSERT INTO lib_albums (id, artist_id, title, local_title, title_lower, source_platform) "
        "VALUES ('al-1', 'ar-1', 'OK Computer', 'OK Computer', 'ok computer', 'navidrome')"
    )
    conn.commit()
    conn.close()

    with patch("app.db.navidrome_reader.config") as mock_config:
        mock_config.RYTHMX_DB = tmp_db
        import app.db.navidrome_reader as reader
        result = reader.check_album_owned("Radiohead", "OK Computer")
        assert result == "al-1"

        result_miss = reader.check_album_owned("Radiohead", "Pablo Honey")
        assert result_miss is None
