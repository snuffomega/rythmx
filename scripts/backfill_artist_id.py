"""
Backfill NULL artist_id in lib_releases.

Run AFTER migration 027 (which creates migration_audit table).
Safe to run multiple times — idempotent.

Usage:
    python scripts/backfill_artist_id.py
"""
import sqlite3
import pathlib
import sys

DB_PATH = pathlib.Path("rythmx.db")

if not DB_PATH.exists():
    print(f"ERROR: {DB_PATH} not found. Run from the project root.")
    sys.exit(1)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.row_factory = sqlite3.Row

    # ── Step 1: Audit current state ──────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM lib_releases").fetchone()[0]
    null_count = conn.execute(
        "SELECT COUNT(*) FROM lib_releases WHERE artist_id IS NULL"
    ).fetchone()[0]
    print(f"lib_releases: {total} total, {null_count} with NULL artist_id")

    if null_count == 0:
        print("Nothing to backfill. All rows have artist_id set.")
        conn.close()
        return

    # ── Step 2: Deterministic backfill (single-match only) ───────
    # Find releases where artist_name_lower matches exactly one lib_artists row
    backfill_sql = """
        WITH candidates AS (
            SELECT lr.id AS release_id, la.id AS artist_id
            FROM lib_releases lr
            JOIN lib_artists la ON la.name_lower = lr.artist_name_lower
            WHERE lr.artist_id IS NULL
        ),
        unique_matches AS (
            SELECT release_id, artist_id
            FROM candidates
            GROUP BY release_id
            HAVING COUNT(*) = 1
        )
        SELECT release_id, artist_id FROM unique_matches
    """
    matches = conn.execute(backfill_sql).fetchall()
    print(f"  Deterministic matches (single-artist): {len(matches)}")

    for row in matches:
        conn.execute(
            "UPDATE lib_releases SET artist_id = ? WHERE id = ?",
            (row["artist_id"], row["release_id"]),
        )
        conn.execute(
            """INSERT INTO migration_audit(action, table_name, row_id, details)
               VALUES ('backfill_artist_id', 'lib_releases', ?, ?)""",
            (row["release_id"], f"matched to artist_id={row['artist_id']}"),
        )

    print(f"  Updated {len(matches)} rows.")

    # ── Step 3: Surface ambiguous matches ────────────────────────
    ambiguous_sql = """
        SELECT lr.id, lr.artist_name,
               GROUP_CONCAT(la.id || ':' || la.name, ' | ') AS candidates
        FROM lib_releases lr
        JOIN lib_artists la ON la.name_lower = lr.artist_name_lower
        WHERE lr.artist_id IS NULL
        GROUP BY lr.id
        HAVING COUNT(la.id) > 1
    """
    ambiguous = conn.execute(ambiguous_sql).fetchall()
    if ambiguous:
        print(f"\n  Ambiguous matches ({len(ambiguous)} rows — multiple artist candidates):")
        for row in ambiguous:
            print(f"    {row['id']}: '{row['artist_name']}' → [{row['candidates']}]")

    # ── Step 4: Delete remaining orphans (no artist match at all) ─
    remaining = conn.execute(
        "SELECT COUNT(*) FROM lib_releases WHERE artist_id IS NULL"
    ).fetchone()[0]

    if remaining > 0:
        # Show what we're about to delete
        orphans = conn.execute(
            """SELECT id, artist_name, title FROM lib_releases
               WHERE artist_id IS NULL LIMIT 20"""
        ).fetchall()
        print(f"\n  Orphan rows with no artist match: {remaining}")
        for row in orphans:
            print(f"    {row['id']}: '{row['artist_name']}' — {row['title']}")
        if remaining > 20:
            print(f"    ... and {remaining - 20} more")

        # Log deletions
        conn.execute(
            """INSERT INTO migration_audit(action, table_name, row_id, details)
               SELECT 'delete_orphan', 'lib_releases', id,
                      'no artist match: ' || artist_name
               FROM lib_releases WHERE artist_id IS NULL"""
        )
        conn.execute("DELETE FROM lib_releases WHERE artist_id IS NULL")
        print(f"  Deleted {remaining} orphan rows.")

    # ── Step 5: Final verification ───────────────────────────────
    final_null = conn.execute(
        "SELECT COUNT(*) FROM lib_releases WHERE artist_id IS NULL"
    ).fetchone()[0]
    final_total = conn.execute("SELECT COUNT(*) FROM lib_releases").fetchone()[0]

    conn.commit()
    conn.close()

    print(f"\nDone. lib_releases: {final_total} rows, {final_null} with NULL artist_id.")
    if final_null > 0:
        print("WARNING: Still have NULL artist_id rows — investigate before running 028.")
        sys.exit(1)
    else:
        print("All clear — safe to apply 028_lib_releases_not_null.sql")


if __name__ == "__main__":
    main()
