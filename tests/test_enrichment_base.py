"""
test_enrichment_base.py — Unit tests for run_enrichment_loop() and
write_enrichment_meta() in app/services/enrichment/_base.py.

TestRunEnrichmentLoop uses the tmp_db fixture (full schema in a temp file DB)
so _connect() calls work naturally without patching.

TestWriteEnrichmentMeta operates against a minimal in-memory SQLite DB —
no fixtures needed.
"""
import sqlite3
import threading

import pytest

from app.services.enrichment._base import run_enrichment_loop, write_enrichment_meta


# ---------------------------------------------------------------------------
# TestRunEnrichmentLoop
# ---------------------------------------------------------------------------

class TestRunEnrichmentLoop:
    """
    Uses tmp_db so that _base._connect() hits a real (temp) file DB.
    Creates a 'test_items' table for candidate rows.
    """

    _CANDIDATE_SQL = "SELECT id, name FROM test_items"
    _REMAINING_SQL = "SELECT COUNT(*) FROM test_items"

    def _create_table(self, tmp_db: str) -> None:
        """Create the test_items scratch table in the temp DB."""
        conn = sqlite3.connect(tmp_db)
        conn.execute("CREATE TABLE IF NOT EXISTS test_items (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        conn.commit()
        conn.close()

    def _seed(self, tmp_db: str, *rows: tuple) -> None:
        conn = sqlite3.connect(tmp_db)
        for row in rows:
            conn.execute("INSERT INTO test_items VALUES (?, ?)", row)
        conn.commit()
        conn.close()

    def _run(self, process_item, **kwargs):
        return run_enrichment_loop(
            worker_name="test-worker",
            candidate_sql=self._CANDIDATE_SQL,
            remaining_sql=self._REMAINING_SQL,
            source="test",
            entity_type="artist",
            entity_id_col="id",
            process_item=process_item,
            **kwargs,
        )

    def test_all_found_counted_correctly(self, tmp_db):
        """process_item returning 'found' for all rows → enriched == len(rows)."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "Artist One"), ("a2", "Artist Two"))

        result = self._run(lambda conn, row: "found")

        assert result["enriched"] == 2
        assert result["failed"] == 0
        assert result["skipped"] == 0

    def test_not_found_counted_as_skipped(self, tmp_db):
        """process_item returning 'not_found' → skipped counter increments."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "Artist One"))

        result = self._run(lambda conn, row: "not_found")

        assert result["skipped"] == 1
        assert result["enriched"] == 0

    def test_failed_item_does_not_stop_loop(self, tmp_db):
        """A process_item exception is caught; remaining items still run."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "Fail"), ("a2", "Succeed"))
        call_count = [0]

        def process_item(conn, row):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("simulated failure")
            return "found"

        result = self._run(process_item)

        assert call_count[0] == 2
        assert result["failed"] == 1
        assert result["enriched"] == 1

    def test_failed_item_writes_error_meta(self, tmp_db):
        """A process_item exception causes an 'error' row in enrichment_meta."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "Artist One"))

        def process_item(conn, row):
            raise RuntimeError("api timeout")

        self._run(process_item)

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM enrichment_meta WHERE entity_id = 'a1'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "error"

    def test_stop_event_halts_after_first_item(self, tmp_db):
        """Setting stop_event inside process_item stops subsequent items."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "First"), ("a2", "Second"))
        stop = threading.Event()
        call_count = [0]

        def process_item(conn, row):
            call_count[0] += 1
            stop.set()
            return "found"

        self._run(process_item, stop_event=stop)

        assert call_count[0] == 1

    def test_empty_candidates_returns_zero_counts(self, tmp_db):
        """No candidates → all counters are 0 and remaining is 0."""
        self._create_table(tmp_db)

        result = self._run(lambda conn, row: "found")

        assert result == {"enriched": 0, "skipped": 0, "failed": 0, "remaining": 0}

    def test_on_progress_called_per_item(self, tmp_db):
        """on_progress callback receives updated counts after each item."""
        self._create_table(tmp_db)
        self._seed(tmp_db, ("a1", "First"), ("a2", "Second"))
        progress_calls = []

        def on_progress(found, not_found, errors, total):
            progress_calls.append((found, not_found, errors, total))

        self._run(lambda conn, row: "found", on_progress=on_progress)

        assert len(progress_calls) == 2
        assert progress_calls[-1] == (2, 0, 0, 2)


# ---------------------------------------------------------------------------
# TestWriteEnrichmentMeta
# ---------------------------------------------------------------------------

class TestWriteEnrichmentMeta:
    """Uses an isolated in-memory DB — no tmp_db dependency needed."""

    _DDL = """
        CREATE TABLE enrichment_meta (
            source TEXT, entity_type TEXT, entity_id TEXT,
            status TEXT, enriched_at TEXT, error_msg TEXT,
            confidence INTEGER, retry_after TEXT,
            PRIMARY KEY (source, entity_type, entity_id)
        )
    """

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(self._DDL)
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def _get(self, entity_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM enrichment_meta WHERE entity_id = ?", (entity_id,)
        ).fetchone()

    def test_writes_found_status(self):
        write_enrichment_meta(self.conn, "itunes", "artist", "a1", "found")
        self.conn.commit()
        assert self._get("a1")["status"] == "found"

    def test_not_found_sets_retry_after(self):
        write_enrichment_meta(self.conn, "itunes", "artist", "a1", "not_found")
        self.conn.commit()
        assert self._get("a1")["retry_after"] is not None

    def test_found_does_not_set_retry_after(self):
        write_enrichment_meta(self.conn, "itunes", "artist", "a1", "found")
        self.conn.commit()
        assert self._get("a1")["retry_after"] is None

    def test_error_status_stores_error_msg(self):
        write_enrichment_meta(
            self.conn, "itunes", "artist", "a1", "error", error_msg="connection timeout"
        )
        self.conn.commit()
        row = self._get("a1")
        assert row["status"] == "error"
        assert "timeout" in (row["error_msg"] or "")

    def test_upsert_overwrites_previous_status(self):
        write_enrichment_meta(self.conn, "itunes", "artist", "a1", "not_found")
        self.conn.commit()
        write_enrichment_meta(self.conn, "itunes", "artist", "a1", "found")
        self.conn.commit()
        assert self._get("a1")["status"] == "found"

    def test_missing_table_is_silently_ignored(self):
        """write_enrichment_meta swallows exceptions when the table is absent."""
        conn = sqlite3.connect(":memory:")
        write_enrichment_meta(conn, "itunes", "artist", "a1", "found")  # no table — must not raise
        conn.close()
