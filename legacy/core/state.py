from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.db import Database


class StateStore:
    """
    Tracking store.

    backends:
      - file: write JSONL only
      - postgres: write DB only
      - dual: write DB + JSONL (DB best-effort)
    """

    def __init__(self, backend: str = "dual", state_dir: str = "./state"):
        self.backend = (backend or "dual").strip().lower()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # DB should exist for postgres/dual
        self.db: Optional[Database] = Database() if self.backend in ("postgres", "dual") else None

        self.events_path = self.state_dir / "events.jsonl"

    # -------------------------
    # JSONL helpers
    # -------------------------
    def _now_utc_ts(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _write_event(self, event: Dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("ts", self._now_utc_ts())
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # -------------------------
    # Seen checks
    # -------------------------
    def has_seen_release(self, spotify_album_id: str) -> bool:
        """
        Returns True if we've already recorded this album id.
        Prefers DB when available (postgres/dual), falls back to JSONL.
        """
        if not spotify_album_id:
            return False

        # DB check (fast + authoritative)
        if self.db is not None:
            try:
                rows = self.db.execute(
                    """
                    SELECT 1
                    FROM release
                    WHERE spotify_album_id = %s
                    LIMIT 1
                    """,
                    (spotify_album_id,),
                )
                return bool(rows)
            except Exception:
                # fall through to JSONL
                pass

        # JSONL fallback
        if self.events_path.exists():
            try:
                with self.events_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("event") == "release_seen" and evt.get("spotify_album_id") == spotify_album_id:
                            return True
            except Exception:
                pass

        return False

    def has_seen_track(self, spotify_track_id: str) -> bool:
        """
        Returns True if we've already recorded this track id.
        Prefers DB when available (postgres/dual), falls back to JSONL.
        """
        if not spotify_track_id:
            return False

        # DB check
        if self.db is not None:
            try:
                rows = self.db.execute(
                    """
                    SELECT 1
                    FROM track
                    WHERE spotify_track_id = %s
                    LIMIT 1
                    """,
                    (spotify_track_id,),
                )
                return bool(rows)
            except Exception:
                pass

        # JSONL fallback
        if self.events_path.exists():
            try:
                with self.events_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("event") == "track_seen" and evt.get("spotify_track_id") == spotify_track_id:
                            return True
            except Exception:
                pass

        return False

    # -------------------------
    # Mark seen
    # -------------------------
    def mark_release_seen(self, spotify_album_id: str, meta: Dict[str, Any] | None = None) -> None:
        """
        Record that we saw an album release.

        - Writes JSONL when backend is file/dual
        - Writes Postgres when backend is postgres/dual

        NOTE: Your DB schema currently does NOT include an artist_name column,
        so we store it (and anything else) inside meta JSON.
        """
        if not spotify_album_id:
            return

        meta = meta or {}

        # Always write JSONL for file/dual
        if self.backend in ("file", "dual"):
            self._write_event(
                {
                    "event": "release_seen",
                    "spotify_album_id": spotify_album_id,
                    "meta": meta,
                }
            )

        # DB for postgres/dual (best-effort)
        if self.backend in ("postgres", "dual") and self.db is not None:
            try:
                self.db.execute(
                    """
                    INSERT INTO release (
                        spotify_album_id,
                        spotify_artist_id,
                        album_name,
                        release_date,
                        album_type,
                        first_seen_at,
                        last_seen_at,
                        meta
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s::jsonb)
                    ON CONFLICT (spotify_album_id) DO UPDATE SET
                        spotify_artist_id = COALESCE(EXCLUDED.spotify_artist_id, release.spotify_artist_id),
                        album_name        = COALESCE(EXCLUDED.album_name,        release.album_name),
                        release_date      = COALESCE(EXCLUDED.release_date,      release.release_date),
                        album_type        = COALESCE(EXCLUDED.album_type,        release.album_type),
                        last_seen_at      = NOW(),
                        meta              = COALESCE(release.meta, '{}'::jsonb) || COALESCE(EXCLUDED.meta, '{}'::jsonb)
                    """,
                    (
                        spotify_album_id,
                        meta.get("spotify_artist_id"),
                        meta.get("album_name"),
                        meta.get("release_date"),
                        meta.get("album_type"),
                        json.dumps(meta),
                    ),
                )
            except Exception:
                # non-fatal; JSONL already captured it (for dual/file)
                pass

    def mark_track_seen(self, spotify_track_id: str, meta: Dict[str, Any] | None = None) -> None:
        """
        Record that we saw a track.

        - Writes JSONL when backend is file/dual
        - Writes Postgres when backend is postgres/dual
        """
        if not spotify_track_id:
            return

        meta = meta or {}

        # Always write JSONL for file/dual
        if self.backend in ("file", "dual"):
            self._write_event(
                {
                    "event": "track_seen",
                    "spotify_track_id": spotify_track_id,
                    "meta": meta,
                }
            )

        # DB for postgres/dual (best-effort)
        if self.backend in ("postgres", "dual") and self.db is not None:
            try:
                self.db.execute(
                    """
                    INSERT INTO track (
                        spotify_track_id,
                        spotify_album_id,
                        spotify_artist_id,
                        track_name,
                        isrc,
                        first_seen_at,
                        last_seen_at,
                        meta
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s::jsonb)
                    ON CONFLICT (spotify_track_id) DO UPDATE SET
                        spotify_album_id  = COALESCE(EXCLUDED.spotify_album_id,  track.spotify_album_id),
                        spotify_artist_id = COALESCE(EXCLUDED.spotify_artist_id, track.spotify_artist_id),
                        track_name        = COALESCE(EXCLUDED.track_name,        track.track_name),
                        isrc              = COALESCE(EXCLUDED.isrc,              track.isrc),
                        last_seen_at      = NOW(),
                        meta              = COALESCE(track.meta, '{}'::jsonb) || COALESCE(EXCLUDED.meta, '{}'::jsonb)
                    """,
                    (
                        spotify_track_id,
                        meta.get("spotify_album_id"),
                        meta.get("spotify_artist_id"),
                        meta.get("track_name"),
                        meta.get("isrc"),
                        json.dumps(meta),
                    ),
                )
            except Exception:
                pass
