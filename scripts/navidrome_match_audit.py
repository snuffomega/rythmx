"""
Navidrome matching audit for Rythmx.

Usage examples:
  python scripts/navidrome_match_audit.py --rythmx-db "\\\\10.10.1.20\\appdata\\rythmx_navidrome\\data\\rythmx.db" --navidrome-db "data/navidrome.db"
  python scripts/navidrome_match_audit.py --rythmx-db data/rythmx.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.enrichment._helpers import match_album_title


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _owner_parity(conn: sqlite3.Connection, navidrome_db: str | None) -> tuple[int | None, int | None]:
    if not navidrome_db:
        return None, None
    if not Path(navidrome_db).exists():
        return None, None

    conn.execute("ATTACH DATABASE ? AS nav", (navidrome_db,))
    album_mismatch = conn.execute(
        """
        SELECT COUNT(*)
        FROM lib_albums la
        JOIN nav.album na ON na.id = la.id
        WHERE la.removed_at IS NULL
          AND COALESCE(la.artist_id, '') <> COALESCE(na.album_artist_id, '')
        """
    ).fetchone()[0]
    track_mismatch = conn.execute(
        """
        SELECT COUNT(*)
        FROM lib_tracks lt
        JOIN nav.media_file mf ON mf.id = lt.id
        WHERE lt.removed_at IS NULL
          AND COALESCE(lt.artist_id, '') <> COALESCE(mf.artist_id, '')
        """
    ).fetchone()[0]
    return album_mismatch, track_mismatch


def run_audit(
    rythmx_db: str,
    navidrome_db: str | None,
    threshold: float,
    near_threshold: float,
    sample: int,
) -> None:
    conn = _connect(rythmx_db)

    counts = {
        "active_artists": conn.execute(
            "SELECT COUNT(*) FROM lib_artists WHERE removed_at IS NULL"
        ).fetchone()[0],
        "active_albums": conn.execute(
            "SELECT COUNT(*) FROM lib_albums WHERE removed_at IS NULL"
        ).fetchone()[0],
        "active_tracks": conn.execute(
            "SELECT COUNT(*) FROM lib_tracks WHERE removed_at IS NULL"
        ).fetchone()[0],
        "unmatched_albums": conn.execute(
            """
            SELECT COUNT(*)
            FROM lib_albums
            WHERE removed_at IS NULL
              AND itunes_album_id IS NULL
              AND deezer_id IS NULL
            """
        ).fetchone()[0],
        "verification_queue": conn.execute(
            """
            SELECT COUNT(*)
            FROM lib_albums
            WHERE removed_at IS NULL
              AND needs_verification = 1
            """
        ).fetchone()[0],
    }

    album_mismatch, track_mismatch = _owner_parity(conn, navidrome_db)

    unmatched_rows = conn.execute(
        """
        SELECT la.id AS album_id,
               la.artist_id,
               ar.name AS artist_name,
               COALESCE(la.local_title, la.title) AS lib_title
        FROM lib_albums la
        JOIN lib_artists ar ON ar.id = la.artist_id
        WHERE la.removed_at IS NULL
          AND la.itunes_album_id IS NULL
          AND la.deezer_id IS NULL
        ORDER BY ar.name, lib_title
        """
    ).fetchall()

    artist_ids = sorted({row["artist_id"] for row in unmatched_rows})
    catalog_by_artist: dict[str, list[sqlite3.Row]] = defaultdict(list)
    if artist_ids:
        placeholders = ",".join("?" for _ in artist_ids)
        catalog_rows = conn.execute(
            f"""
            SELECT artist_id, source, album_id, album_title
            FROM lib_artist_catalog
            WHERE source IN ('itunes', 'deezer')
              AND album_title IS NOT NULL
              AND album_title <> ''
              AND artist_id IN ({placeholders})
            """,
            artist_ids,
        ).fetchall()
        for row in catalog_rows:
            catalog_by_artist[row["artist_id"]].append(row)

    bucket_counts: Counter[str] = Counter()
    bucket_examples: dict[str, list[str]] = defaultdict(list)
    unmatched_by_artist: Counter[str] = Counter()
    artist_meta: dict[str, tuple[str, int | None, bool, bool]] = {}

    for row in unmatched_rows:
        artist_id = row["artist_id"]
        artist_name = row["artist_name"]
        lib_title = row["lib_title"]
        unmatched_by_artist[artist_name] += 1

        if artist_id not in artist_meta:
            meta = conn.execute(
                """
                SELECT name, match_confidence, itunes_artist_id, deezer_artist_id
                FROM lib_artists
                WHERE id = ?
                """,
                (artist_id,),
            ).fetchone()
            if meta:
                artist_meta[artist_id] = (
                    meta["name"],
                    meta["match_confidence"],
                    bool(meta["itunes_artist_id"]),
                    bool(meta["deezer_artist_id"]),
                )

        artist_catalog = catalog_by_artist.get(artist_id, [])
        if not artist_catalog:
            bucket = "no_catalog_any_source"
            bucket_counts[bucket] += 1
            if len(bucket_examples[bucket]) < sample:
                bucket_examples[bucket].append(f"{artist_name} :: {lib_title}")
            continue

        best = ("", "", "", 0.0)
        for entry in artist_catalog:
            score = match_album_title(lib_title, entry["album_title"])
            if score > best[3]:
                best = (entry["source"], entry["album_id"], entry["album_title"], score)

        if best[3] >= threshold:
            bucket = "should_match_now_threshold_passed"
            desc = (
                f"{artist_name} :: {lib_title} => "
                f"[{best[0]}] {best[2]} (id={best[1]}, score={best[3]:.3f})"
            )
        elif best[3] >= near_threshold:
            bucket = "near_threshold"
            desc = (
                f"{artist_name} :: {lib_title} => "
                f"[{best[0]}] {best[2]} (id={best[1]}, score={best[3]:.3f})"
            )
        else:
            bucket = "below_threshold"
            desc = (
                f"{artist_name} :: {lib_title} => "
                f"[{best[0]}] {best[2]} (id={best[1]}, score={best[3]:.3f})"
            )
        bucket_counts[bucket] += 1
        if len(bucket_examples[bucket]) < sample:
            bucket_examples[bucket].append(desc)

    print("Navidrome Match Audit")
    print(f"- rythmx_db: {rythmx_db}")
    if navidrome_db:
        print(f"- navidrome_db: {navidrome_db}")
    print("")
    print("Library Counts")
    print(f"- active_artists: {counts['active_artists']}")
    print(f"- active_albums: {counts['active_albums']}")
    print(f"- active_tracks: {counts['active_tracks']}")
    print(f"- unmatched_albums: {counts['unmatched_albums']}")
    print(f"- verification_queue: {counts['verification_queue']}")
    if album_mismatch is not None and track_mismatch is not None:
        print(f"- album_owner_mismatch: {album_mismatch}")
        print(f"- track_owner_mismatch: {track_mismatch}")
    print("")
    print("Unmatched Buckets")
    print(f"- no_catalog_any_source: {bucket_counts['no_catalog_any_source']}")
    print(f"- should_match_now_threshold_passed: {bucket_counts['should_match_now_threshold_passed']}")
    print(f"- near_threshold ({near_threshold:.2f}-{threshold:.3f}): {bucket_counts['near_threshold']}")
    print(f"- below_threshold (<{near_threshold:.2f}): {bucket_counts['below_threshold']}")

    for bucket_name in [
        "should_match_now_threshold_passed",
        "near_threshold",
        "below_threshold",
        "no_catalog_any_source",
    ]:
        items = bucket_examples.get(bucket_name, [])
        if not items:
            continue
        print("")
        print(f"{bucket_name} examples")
        for item in items:
            print(f"- {item}")

    print("")
    print("Artists With Unmatched Albums")
    artist_rows = conn.execute(
        """
        SELECT ar.id,
               ar.name,
               ar.match_confidence,
               ar.itunes_artist_id,
               ar.deezer_artist_id,
               SUM(CASE WHEN la.itunes_album_id IS NULL AND la.deezer_id IS NULL THEN 1 ELSE 0 END) AS unmatched_count,
               COUNT(*) AS total_albums
        FROM lib_artists ar
        JOIN lib_albums la ON la.artist_id = ar.id
        WHERE la.removed_at IS NULL
        GROUP BY ar.id, ar.name, ar.match_confidence, ar.itunes_artist_id, ar.deezer_artist_id
        HAVING unmatched_count > 0
        ORDER BY unmatched_count DESC, ar.name
        """
    ).fetchall()
    for row in artist_rows[: max(sample, 10)]:
        conf = row["match_confidence"]
        print(
            f"- {row['name']}: unmatched={row['unmatched_count']}/{row['total_albums']}, "
            f"conf={conf}, itunes_id={'Y' if row['itunes_artist_id'] else 'N'}, "
            f"deezer_id={'Y' if row['deezer_artist_id'] else 'N'}"
        )

    low_conf = [r["name"] for r in artist_rows if (r["match_confidence"] or 0) <= 70]
    if low_conf:
        print("")
        print(f"Low-confidence artists (<=70): {len(low_conf)}")
        for name in low_conf[: max(sample, 10)]:
            print(f"- {name}")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Navidrome matching accuracy in Rythmx.")
    parser.add_argument(
        "--rythmx-db",
        default=os.getenv("RYTHMX_DB", "data/rythmx.db"),
        help="Path to Rythmx DB (default: RYTHMX_DB env or data/rythmx.db).",
    )
    parser.add_argument(
        "--navidrome-db",
        default="data/navidrome.db",
        help="Optional Navidrome DB path for owner parity checks.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.82,
        help="Match threshold for should-match-now bucket.",
    )
    parser.add_argument(
        "--near-threshold",
        type=float,
        default=0.75,
        help="Near-threshold lower bound.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=10,
        help="Example rows per bucket section.",
    )
    args = parser.parse_args()

    nav_db = args.navidrome_db or None
    if nav_db and not Path(nav_db).exists():
        nav_db = None

    run_audit(
        rythmx_db=args.rythmx_db,
        navidrome_db=nav_db,
        threshold=args.threshold,
        near_threshold=args.near_threshold,
        sample=max(1, args.sample),
    )


if __name__ == "__main__":
    main()
