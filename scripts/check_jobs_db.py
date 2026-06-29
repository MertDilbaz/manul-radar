"""Inspect data/jobs.db to detect any dummy-source contamination.

Detailed view: list the dummy rows (must be cleaned) and the real rows
(must be preserved) side by side so an operator can confirm what the
cache contains before deciding whether to wipe it.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/jobs.db")

if not DB_PATH.exists():
    print(f"NO_DB: {DB_PATH.resolve()} does not exist")
    raise SystemExit(0)

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)

if "jobs" not in tables:
    print("NO_JOBS_TABLE")
    raise SystemExit(0)

cur.execute("SELECT COUNT(*) FROM jobs")
total = cur.fetchone()[0]
print(f"TOTAL_JOBS: {total}")

# Look for the canonical dummy-source company names.
DUMMY_MARKERS = ["SpringyCorp", "MegaScale", "ERPify"]
for marker in DUMMY_MARKERS:
    cur.execute("SELECT COUNT(*) FROM jobs WHERE company LIKE ?", (f"%{marker}%",))
    count = cur.fetchone()[0]
    print(f"DUMMY_HIT[{marker}]: {count}")

# Look for source == 'dummy' (the name DummySource registers itself with).
cur.execute("SELECT COUNT(*) FROM jobs WHERE source = 'dummy'")
count = cur.fetchone()[0]
print(f"DUMMY_SOURCE_ROWS: {count}")

# Side-by-side detail: dummy rows to be cleaned vs real rows to keep.
cur.execute(
    "SELECT id, source, company, title FROM jobs WHERE source = 'dummy'"
)
dummy_rows = cur.fetchall()
print("\nDUMMY_ROWS (must be cleaned):")
for r in dummy_rows:
    print(" ", r)

cur.execute(
    "SELECT id, source, company, title FROM jobs WHERE source != 'dummy' ORDER BY id"
)
real_rows = cur.fetchall()
print("\nREAL_ROWS (must be preserved):")
for r in real_rows:
    print(" ", r)

# Also check for the rejected_jobs or run_log tables — these can accumulate
# state too, so it's worth surfacing them so a wipe decision is informed.
for table in ("run_log", "rejected_jobs", "seen_jobs"):
    if table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        cnt = cur.fetchone()[0]
        print(f"{table}: {cnt} row(s)")

conn.close()