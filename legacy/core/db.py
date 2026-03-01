# app/core/db.py
from __future__ import annotations

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

from core.config import CONFIG


class Database:
    """
    Lightweight Postgres helper.
    - Reads connection info from core.config.CONFIG
    - Autocommit off (we commit after execute/executes)
    """

    def __init__(self) -> None:
        self._conn = None

    def connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=CONFIG["pg_host"],
                port=CONFIG["pg_port"],
                dbname=CONFIG["pg_db"],
                user=CONFIG["pg_user"],
                password=CONFIG["pg_password"],
                sslmode=CONFIG["pg_sslmode"],
            )
            self._conn.autocommit = False
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def execute(self, sql: str, params=None):
        conn = self.connect()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = []
                if cur.description:
                    rows = cur.fetchall()
            conn.commit()
            return rows
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    def executemany(self, sql: str, params_seq: Iterable[Any]) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, params_seq, page_size=200)
            conn.commit()
