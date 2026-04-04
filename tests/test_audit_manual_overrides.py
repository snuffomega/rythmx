from __future__ import annotations

import sqlite3

from app.routes.library import audit
from app.services.enrichment import id_itunes_deezer


def _connect_factory(db_path: str):
    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    return _connect


def _init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE lib_albums (
            id TEXT PRIMARY KEY,
            itunes_album_id TEXT,
            deezer_id TEXT,
            match_confidence INTEGER DEFAULT 0,
            needs_verification INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.commit()
    conn.close()


def test_confirm_writes_override_event_and_manual_meta(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit_confirm.db")
    _init_db(db_path)
    monkeypatch.setattr(audit.rythmx_store, "_connect", _connect_factory(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO lib_albums (id, match_confidence, needs_verification) VALUES ('al1', 40, 1)"
    )
    conn.commit()
    conn.close()

    result = audit.library_audit_confirm(
        {
            "entity_type": "album",
            "entity_id": "al1",
            "source": "ITUNES",
            "confirmed_id": "12345",
            "note": "manual pick",
            "actor": "tester",
        }
    )
    assert result["status"] == "ok"

    conn = sqlite3.connect(db_path)
    album = conn.execute(
        "SELECT itunes_album_id, match_confidence, needs_verification FROM lib_albums WHERE id='al1'"
    ).fetchone()
    override = conn.execute(
        "SELECT state, confirmed_id, locked, note, updated_by FROM match_overrides WHERE entity_type='album' AND entity_id='al1' AND source='itunes'"
    ).fetchone()
    event = conn.execute(
        "SELECT action, candidate_id, note, actor FROM match_override_events WHERE entity_id='al1' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    meta = conn.execute(
        "SELECT status, confidence, error_msg, verified_at, retry_after FROM enrichment_meta WHERE source='itunes' AND entity_type='album' AND entity_id='al1'"
    ).fetchone()
    conn.close()

    assert album == ("12345", 100, 0)
    assert override == ("confirmed", "12345", 1, "manual pick", "tester")
    assert event == ("confirm", "12345", "manual pick", "tester")
    assert meta[0] == "found"
    assert meta[1] == 100
    assert meta[2] == "manual_confirm"
    assert meta[3] is not None
    assert meta[4] is None


def test_reject_only_zeroes_confidence_when_both_sources_missing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit_reject.db")
    _init_db(db_path)
    monkeypatch.setattr(audit.rythmx_store, "_connect", _connect_factory(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO lib_albums (id, itunes_album_id, deezer_id, match_confidence, needs_verification)
        VALUES ('al_keep', 'it_1', 'dz_1', 91, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO lib_albums (id, itunes_album_id, deezer_id, match_confidence, needs_verification)
        VALUES ('al_zero', 'it_2', NULL, 88, 0)
        """
    )
    conn.commit()
    conn.close()

    keep_result = audit.library_audit_reject(
        {
            "entity_type": "album",
            "entity_id": "al_keep",
            "source": "itunes",
            "candidate_id": "it_1",
            "note": "wrong edition",
            "actor": "tester",
        }
    )
    zero_result = audit.library_audit_reject(
        {
            "entity_type": "album",
            "entity_id": "al_zero",
            "source": "itunes",
        }
    )
    assert keep_result["status"] == "ok"
    assert zero_result["status"] == "ok"

    conn = sqlite3.connect(db_path)
    keep_album = conn.execute(
        "SELECT itunes_album_id, deezer_id, match_confidence, needs_verification FROM lib_albums WHERE id='al_keep'"
    ).fetchone()
    zero_album = conn.execute(
        "SELECT itunes_album_id, deezer_id, match_confidence, needs_verification FROM lib_albums WHERE id='al_zero'"
    ).fetchone()
    keep_meta = conn.execute(
        "SELECT status, confidence, error_msg, retry_after, verified_at FROM enrichment_meta WHERE source='itunes' AND entity_id='al_keep'"
    ).fetchone()
    unlock_before = conn.execute(
        "SELECT locked FROM match_overrides WHERE entity_type='album' AND entity_id='al_keep' AND source='itunes'"
    ).fetchone()
    conn.close()

    assert keep_album == (None, "dz_1", 91, 1)
    assert zero_album == (None, None, 0, 1)
    assert keep_meta[0] == "not_found"
    assert keep_meta[1] == 0
    assert keep_meta[2] == "manual_reject"
    assert keep_meta[3] is not None
    assert keep_meta[4] is not None
    assert unlock_before == (1,)


def test_unlock_clears_lock_and_logs_event(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit_unlock.db")
    _init_db(db_path)
    monkeypatch.setattr(audit.rythmx_store, "_connect", _connect_factory(db_path))

    # Seed a manual lock via confirm route so unlock has a target row.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO lib_albums (id, match_confidence, needs_verification) VALUES ('al1', 0, 1)"
    )
    conn.commit()
    conn.close()
    audit.library_audit_confirm(
        {"entity_type": "album", "entity_id": "al1", "source": "deezer", "confirmed_id": "dz_55"}
    )

    result = audit.library_audit_unlock(
        {
            "entity_type": "album",
            "entity_id": "al1",
            "source": "deezer",
            "note": "allow retry",
            "actor": "tester",
        }
    )
    assert result["status"] == "ok"

    conn = sqlite3.connect(db_path)
    override = conn.execute(
        "SELECT locked, note, updated_by FROM match_overrides WHERE entity_type='album' AND entity_id='al1' AND source='deezer'"
    ).fetchone()
    event = conn.execute(
        "SELECT action, note, actor FROM match_override_events WHERE entity_id='al1' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert override == (0, "allow retry", "tester")
    assert event == ("unlock", "allow retry", "tester")


def test_load_album_source_overrides_tolerates_missing_table(tmp_path):
    db_path = str(tmp_path / "guardrail_missing_table.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out = id_itunes_deezer._load_album_source_overrides(conn, ["al1"])
    conn.close()
    assert out == {}
