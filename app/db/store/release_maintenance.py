"""
Release maintenance and normalization helpers for rythmx.db.
"""
from __future__ import annotations

import logging
from typing import Callable

import sqlite3
from app.db.sql_helpers import build_in_clause


def backfill_normalized_titles(connect: Callable[[], sqlite3.Connection], logger: logging.Logger) -> int:
    """Populate normalized_title and version_type for rows missing them."""
    from app.services.enrichment._helpers import detect_version_type
    from app.clients.music_client import norm

    updated = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM lib_releases WHERE normalized_title IS NULL"
        ).fetchall()
        for row in rows:
            cleaned_title, version_type = detect_version_type(row["title"])
            normalized_title = norm(cleaned_title)
            conn.execute(
                "UPDATE lib_releases SET normalized_title = ?, version_type = ? WHERE id = ?",
                (normalized_title, version_type, row["id"]),
            )
            updated += 1
    logger.info("Backfilled normalized_title for %d lib_releases rows", updated)
    return updated


def recompute_normalized_titles(
    connect: Callable[[], sqlite3.Connection],
    logger: logging.Logger,
    artist_ids: list[str] | None = None,
) -> int:
    """Recompute normalized_title and version_type for lib_releases rows."""
    from app.services.enrichment._helpers import detect_version_type
    from app.clients.music_client import norm

    updated = 0
    with connect() as conn:
        if artist_ids:
            rows = conn.execute(
                "SELECT id, title FROM lib_releases WHERE artist_id IN " + build_in_clause(len(artist_ids)),
                artist_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, title FROM lib_releases").fetchall()
        for row in rows:
            cleaned_title, version_type = detect_version_type(row["title"])
            normalized_title = norm(cleaned_title)
            conn.execute(
                "UPDATE lib_releases SET normalized_title = ?, version_type = ? WHERE id = ?",
                (normalized_title, version_type, row["id"]),
            )
            updated += 1
    logger.info(
        "recompute_normalized_titles: reprocessed %d lib_releases rows%s",
        updated,
        f" (scoped to {len(artist_ids)} artists)" if artist_ids else "",
    )
    return updated


def refresh_missing_counts(
    connect: Callable[[], sqlite3.Connection],
    logger: logging.Logger,
    artist_id: str | None = None,
    artist_ids: list[str] | None = None,
) -> int:
    """Recompute lib_artists.missing_count from lib_releases with dedup logic."""
    if artist_ids:
        scope_clause = "WHERE lib_artists.id IN " + build_in_clause(len(artist_ids))
        params: tuple = tuple(artist_ids)
    elif artist_id:
        scope_clause = "WHERE lib_artists.id = ?"
        params = (artist_id,)
    else:
        scope_clause = ""
        params = ()

    with connect() as conn:
        sql = """
            UPDATE lib_artists SET missing_count = COALESCE((
                SELECT COUNT(*) FROM (
                    SELECT id,
                           artist_id,
                           artist_name_lower,
                           normalized_title,
                           COALESCE(
                               kind_deezer, kind_itunes,
                               CASE
                                   WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                   WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                   ELSE 'album'
                               END
                           ) AS resolved_kind,
                           ROW_NUMBER() OVER (
                               PARTITION BY artist_name_lower, normalized_title,
                                            COALESCE(
                                                kind_deezer, kind_itunes,
                                                CASE
                                                    WHEN track_count IS NOT NULL AND track_count <= 3 THEN 'single'
                                                    WHEN track_count IS NOT NULL AND track_count <= 6 THEN 'ep'
                                                    ELSE 'album'
                                                END
                                            )
                               ORDER BY
                                   CASE catalog_source WHEN 'deezer' THEN 1 WHEN 'itunes' THEN 2 ELSE 3 END,
                                   COALESCE(thumb_url_deezer, thumb_url_itunes) IS NOT NULL DESC,
                                   COALESCE(release_date_itunes, release_date_deezer) IS NOT NULL DESC
                           ) AS rn
                    FROM lib_releases
                    WHERE artist_id = lib_artists.id
                      AND is_owned = 0
                      AND user_dismissed = 0
                ) deduped
                WHERE rn = 1
                  AND NOT (
                      resolved_kind = 'single'
                      AND EXISTS (
                          SELECT 1 FROM lib_releases lr2
                          WHERE lr2.artist_id = deduped.artist_id
                            AND lr2.normalized_title = deduped.normalized_title
                            AND COALESCE(lr2.kind_deezer, lr2.kind_itunes, 'album') IN ('album', 'ep')
                            AND lr2.id != deduped.id
                      )
                  )
            ), 0)
        """
        if scope_clause:
            sql += "\n" + scope_clause
        conn.execute(
            sql,
            params,
        )
        updated = conn.execute("SELECT changes()").fetchone()[0]

    scope_msg = ""
    if artist_ids:
        scope_msg = f" (scoped to {len(artist_ids)} artists)"
    elif artist_id:
        scope_msg = f" (artist_id={artist_id})"
    logger.info("refresh_missing_counts: updated %d artists%s", updated, scope_msg)
    return updated


def populate_canonical_release_ids(
    connect: Callable[[], sqlite3.Connection],
    logger: logging.Logger,
    artist_id: str | None = None,
    artist_ids: list[str] | None = None,
) -> int:
    """Assign canonical_release_id to lib_releases rows."""
    where = "WHERE normalized_title IS NOT NULL AND artist_id IS NOT NULL"
    params: tuple = ()
    if artist_ids:
        where += " AND artist_id IN " + build_in_clause(len(artist_ids))
        params = tuple(artist_ids)
    elif artist_id:
        where += " AND artist_id = ?"
        params = (artist_id,)

    with connect() as conn:
        sql = """
            UPDATE lib_releases SET canonical_release_id = (
                SELECT sub.id FROM lib_releases sub
                WHERE sub.artist_id = lib_releases.artist_id
                  AND sub.normalized_title = lib_releases.normalized_title
                  AND sub.normalized_title IS NOT NULL
                ORDER BY
                    sub.is_owned DESC,
                    CASE sub.version_type WHEN 'original' THEN 0 ELSE 1 END,
                    COALESCE(sub.release_date_itunes, sub.release_date_deezer) ASC,
                    CASE sub.catalog_source WHEN 'deezer' THEN 1 ELSE 2 END
                LIMIT 1
            )
        """
        sql += "\n" + where
        cursor = conn.execute(
            sql,
            params,
        )
        updated = cursor.rowcount

    scope_label = f"{len(artist_ids)} artists" if artist_ids else (artist_id or "all")
    logger.info("populate_canonical_release_ids: updated %d rows (scope=%s)", updated, scope_label)
    return updated


def ensure_single_catalog_cleanup(
    connect: Callable[[], sqlite3.Connection],
    logger: logging.Logger,
    primary: str,
) -> None:
    """
    One-time cleanup: remove secondary-source rows from lib_releases.
    Idempotent via app_settings; reruns if primary catalog changes.
    """
    secondary = "itunes" if primary == "deezer" else "deezer"

    with connect() as conn:
        done = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'single_catalog_done'"
        ).fetchone()
        if done and done[0] == primary:
            return

        deleted = conn.execute(
            "DELETE FROM lib_releases WHERE catalog_source = ?",
            (secondary,),
        ).rowcount

        conn.execute(
            "UPDATE lib_releases SET canonical_release_id = NULL, "
            "is_owned = 0, owned_checked_at = NULL"
        )

        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("single_catalog_done", primary),
        )

    logger.info(
        "ensure_single_catalog_cleanup: deleted %d %s rows, primary=%s",
        deleted,
        secondary,
        primary,
    )
