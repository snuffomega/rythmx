"""
migrations/runner.py — SQL migration runner for rythmx.db.

Applies pending .sql files from the migrations/ directory in sorted order.
Tracks applied migrations in the _migrations meta-table within the DB.
Safe to call on every startup — skips already-applied migrations.

Each statement in a .sql file is tried individually. If a statement fails
because the source table doesn't exist (e.g. fresh install where old cc_*
tables were never created), it is skipped with a debug log. Any other
OperationalError is fatal and aborts the migration.
"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.dirname(__file__)


def run_pending_migrations(db_path: str) -> None:
    """Apply any unapplied .sql migrations from the migrations/ directory to db_path."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        applied = {r["name"] for r in conn.execute("SELECT name FROM _migrations").fetchall()}

        sql_files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))

        for filename in sql_files:
            if filename in applied:
                logger.debug("Migration already applied: %s", filename)
                continue

            filepath = os.path.join(MIGRATIONS_DIR, filename)
            with open(filepath, "r") as fh:
                raw = fh.read()

            # Strip comment-only lines and inline comments, then split on semicolons
            lines = []
            for ln in raw.splitlines():
                if ln.strip().startswith("--"):
                    continue
                # Remove inline comments (-- ...) to avoid splitting on embedded semicolons
                comment_idx = ln.find("--")
                if comment_idx >= 0:
                    ln = ln[:comment_idx]
                lines.append(ln)
            statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]

            logger.info("Applying migration: %s (%d statements)", filename, len(statements))
            ok = True
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if "no such table" in msg:
                        # Fresh install — source table never existed; safe to skip this rename
                        logger.debug("Migration stmt skipped (no such table): %.80s", stmt)
                    else:
                        logger.error(
                            "Migration [%s] failed: %s | stmt: %.80s", filename, exc, stmt
                        )
                        ok = False
                        break

            if ok:
                conn.execute("INSERT OR IGNORE INTO _migrations (name) VALUES (?)", (filename,))
                conn.commit()
                logger.info("Migration applied: %s", filename)
            else:
                conn.rollback()
                logger.warning("Migration rolled back (will retry next startup): %s", filename)
    finally:
        conn.close()
