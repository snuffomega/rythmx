from __future__ import annotations

import sqlite3

from app.services.enrichment import sync


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
            name TEXT,
            removed_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE lib_albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT NOT NULL,
            removed_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def test_prune_orphan_artists_tombstones_only_orphans(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sync_prune.db")
    _init_db(db_path)
    monkeypatch.setattr(sync, "_connect", _connect_factory(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO lib_artists (id, name, removed_at) VALUES ('a1', 'Has Album', NULL)")
    conn.execute("INSERT INTO lib_artists (id, name, removed_at) VALUES ('a2', 'No Album', NULL)")
    conn.execute("INSERT INTO lib_albums (id, artist_id, removed_at) VALUES ('al1', 'a1', NULL)")
    conn.commit()
    conn.close()

    pruned = sync._prune_orphan_artists()
    assert pruned == 1

    conn = sqlite3.connect(db_path)
    row_kept = conn.execute("SELECT removed_at FROM lib_artists WHERE id='a1'").fetchone()
    row_pruned = conn.execute("SELECT removed_at FROM lib_artists WHERE id='a2'").fetchone()
    conn.close()

    assert row_kept[0] is None
    assert row_pruned[0] is not None


def test_prune_orphan_artists_ignores_soft_deleted_albums(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sync_prune_removed_album.db")
    _init_db(db_path)
    monkeypatch.setattr(sync, "_connect", _connect_factory(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO lib_artists (id, name, removed_at) VALUES ('a1', 'Only Removed Album', NULL)")
    conn.execute("INSERT INTO lib_albums (id, artist_id, removed_at) VALUES ('al1', 'a1', CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

    pruned = sync._prune_orphan_artists()
    assert pruned == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT removed_at FROM lib_artists WHERE id='a1'").fetchone()
    conn.close()
    assert row[0] is not None

