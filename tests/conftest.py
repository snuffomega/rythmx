"""
conftest.py — Shared pytest fixtures for the Rythmx test suite.

Fixtures
--------
tmp_db      Temporary SQLite DB with full schema (via migrations).
            Monkeypatches config.RYTHMX_DB so all _connect() calls in app
            code hit this temp DB instead of the real database.
            Use for tests that exercise code touching the DB.

db_conn     Raw sqlite3 connection to the tmp_db.
            Useful for seeding test data or running ad-hoc queries.

api_client  Starlette TestClient wrapping the FastAPI app.
            Depends on tmp_db — all DB writes go to the temp DB.
            Use for route-level contract tests.
"""
import sqlite3

import pytest
from starlette.testclient import TestClient


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """
    Create a temporary SQLite database with the full Rythmx schema (run via
    the migration runner) and patch config.RYTHMX_DB for the test's lifetime.

    All code that calls app.db.rythmx_store._connect() will use this temp DB.
    The file is discarded automatically after each test.
    """
    db_path = str(tmp_path / "rythmx_test.db")
    monkeypatch.setattr("app.config.RYTHMX_DB", db_path)

    from migrations.runner import run_pending_migrations
    run_pending_migrations(db_path)

    yield db_path


@pytest.fixture()
def db_conn(tmp_db):
    """
    Open a sqlite3 connection to the tmp_db fixture.

    Useful for inserting seed data before calling service functions under
    test, or for asserting DB state after a call completes.

    The connection is closed automatically after the test.
    """
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture()
def api_client(tmp_db):
    """
    Starlette TestClient wrapping the FastAPI app.

    Depends on tmp_db so all database writes go to the temp database.
    The lifespan hooks run: background daemon threads (scheduler,
    ws-heartbeat) start but are daemon threads and exit with the process.

    Use for route-level contract tests — prefer unit tests with mocks for
    service-level logic.
    """
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
