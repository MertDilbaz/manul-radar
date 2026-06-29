"""Clean dummy-source contamination out of data/jobs.db.

The 2026-06-29 incident left SpringyCorp + ERPify rows in the local
jobs cache from an earlier ``--use-dummy-source`` run. Those rows are
harmless to the Telegram path (URL-based dedup ignores already-seen
URLs even if they came from dummy), but they pollute the persisted
cache and would re-appear in any future cache restore from this
DB. This script removes only ``source = 'dummy'`` rows, leaving real
``lever_*`` records untouched.

USAGE::

    # Dry-run (default): show what would be deleted, change nothing.
    python scripts/clean_jobs_db.py

    # Apply: actually delete the dummy rows. Prompts for confirmation.
    python scripts/clean_jobs_db.py --apply

The script is intentionally narrow (single DELETE statement) and
prints before/after counts so a sanity check is trivial.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/jobs.db")


def _count_rows(cur: sqlite3.Cursor, source: str) -> int:
    cur.execute("SELECT COUNT(*) FROM jobs WHERE source = ?", (source,))
    return int(cur.fetchone()[0])


def _list_rows(cur: sqlite3.Cursor, source: str) -> list[tuple]:
    cur.execute(
        "SELECT id, source, company, title FROM jobs WHERE source = ?",
        (source,),
    )
    return list(cur.fetchall())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove DummySource contamination rows from data/jobs.db. "
            "Real source rows (lever_*, hrpeak_*, etc.) are never touched."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually DELETE the dummy rows. Without this flag the "
        "script only prints what would be removed (dry-run).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt when --apply is set.",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"NO_DB: {DB_PATH.resolve()} does not exist; nothing to do.")
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    before_total = int(cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    dummy_count = _count_rows(cur, "dummy")

    print(f"DB: {DB_PATH.resolve()}")
    print(f"BEFORE: total_jobs={before_total}, dummy_rows={dummy_count}")

    if dummy_count == 0:
        print("CLEAN: no dummy rows to remove.")
        conn.close()
        return 0

    dummy_rows = _list_rows(cur, "dummy")
    print("ROWS THAT WOULD BE DELETED (source='dummy'):")
    for row in dummy_rows:
        print(f"  id={row[0]} source={row[1]} company={row[2]!r} title={row[3]!r}")

    if not args.apply:
        print("\nDRY-RUN: re-run with --apply to actually delete these rows.")
        conn.close()
        return 0

    if not args.yes:
        print()
        reply = input(
            f"About to DELETE {dummy_count} dummy row(s) from "
            f"{DB_PATH}. Real rows will be preserved. Continue? [y/N] "
        )
        if reply.strip().lower() != "y":
            print("ABORTED: no changes made.")
            conn.close()
            return 1

    cur.execute("DELETE FROM jobs WHERE source = 'dummy'")
    conn.commit()

    after_total = int(cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    after_dummy = _count_rows(cur, "dummy")
    print(
        f"AFTER: total_jobs={after_total}, dummy_rows={after_dummy} "
        f"(deleted={before_total - after_total})"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())