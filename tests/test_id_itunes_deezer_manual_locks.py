from __future__ import annotations

import sqlite3

from app.services.enrichment import id_itunes_deezer


def _connect_factory(db_path: str):
    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    return _connect


def _init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE lib_artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            itunes_artist_id TEXT,
            deezer_artist_id TEXT,
            match_confidence INTEGER DEFAULT 0
        );

        CREATE TABLE lib_albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            local_title TEXT,
            itunes_album_id TEXT,
            deezer_id TEXT,
            api_title TEXT,
            match_confidence INTEGER DEFAULT 0,
            needs_verification INTEGER DEFAULT 0,
            removed_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE lib_tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            title TEXT,
            removed_at TEXT
        );

        CREATE TABLE enrichment_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            enriched_at TEXT,
            error_msg TEXT,
            confidence INTEGER,
            retry_after TEXT,
            verified_at TEXT,
            UNIQUE(source, entity_type, entity_id)
        );

        CREATE TABLE lib_artist_catalog (
            artist_id TEXT NOT NULL,
            source TEXT NOT NULL,
            album_id TEXT NOT NULL,
            album_title TEXT NOT NULL,
            record_type TEXT,
            track_count INTEGER,
            artwork_url TEXT,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (artist_id, source, album_id)
        );

        CREATE TABLE match_overrides (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            source TEXT NOT NULL,
            confirmed_id TEXT,
            state TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            updated_by TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_type, entity_id, source)
        );
        """
    )
    conn.commit()
    conn.close()


def test_stage2_respects_manual_reject_and_confirm_locks(tmp_path, monkeypatch):
    db_path = str(tmp_path / "stage2_locks.db")
    _init_db(db_path)
    monkeypatch.setattr(id_itunes_deezer, "_connect", _connect_factory(db_path))
    monkeypatch.setattr(id_itunes_deezer, "promote_catalog_to_releases", lambda *args, **kwargs: None)

    def fake_validate_artist(artist_name: str, _lib_titles: list[str], source: str):
        if source == "itunes":
            return {
                "artist_id": "it_artist_1",
                "confidence": 95,
                "album_catalog": [
                    {"id": "it_auto_1", "title": "Reject Album", "track_count": 10, "record_type": "album"},
                    {"id": "it_auto_2", "title": "Confirm Album", "track_count": 9, "record_type": "album"},
                ],
            }
        return None

    monkeypatch.setattr(id_itunes_deezer, "validate_artist", fake_validate_artist)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO lib_artists (id, name, itunes_artist_id, deezer_artist_id, match_confidence) VALUES ('ar1', 'Lock Artist', NULL, NULL, 0)"
    )
    conn.execute(
        """
        INSERT INTO lib_albums
            (id, artist_id, title, local_title, itunes_album_id, deezer_id, match_confidence, needs_verification, removed_at)
        VALUES
            ('al_reject', 'ar1', 'Reject Album', 'Reject Album', NULL, NULL, 0, 1, NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO lib_albums
            (id, artist_id, title, local_title, itunes_album_id, deezer_id, match_confidence, needs_verification, removed_at)
        VALUES
            ('al_confirm', 'ar1', 'Confirm Album', 'Confirm Album', NULL, NULL, 0, 1, NULL)
        """
    )
    conn.execute(
        "INSERT INTO match_overrides (entity_type, entity_id, source, confirmed_id, state, locked) VALUES ('album', 'al_reject', 'itunes', NULL, 'rejected', 1)"
    )
    conn.execute(
        "INSERT INTO match_overrides (entity_type, entity_id, source, confirmed_id, state, locked) VALUES ('album', 'al_confirm', 'itunes', 'it_manual_99', 'confirmed', 1)"
    )
    conn.commit()
    conn.close()

    result = id_itunes_deezer.enrich_library(batch_size=10)
    assert "error" not in result

    conn = sqlite3.connect(db_path)
    reject_row = conn.execute(
        "SELECT itunes_album_id, needs_verification FROM lib_albums WHERE id='al_reject'"
    ).fetchone()
    confirm_row = conn.execute(
        "SELECT itunes_album_id, needs_verification, match_confidence FROM lib_albums WHERE id='al_confirm'"
    ).fetchone()
    conn.close()

    # Rejected source is not auto-applied.
    assert reject_row == (None, 1)
    # Confirmed lock is authoritative (manual ID wins, verified state retained).
    assert confirm_row == ("it_manual_99", 0, 100)
