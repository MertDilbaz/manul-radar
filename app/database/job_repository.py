"""SQLite-backed repository for processed job postings.

The repository owns the persistence concern for the radar pipeline:
given a ``ScoredJob`` from the filter layer, it can tell you whether
the underlying ``Job`` has been seen before (``has_seen``) and persist
newly-seen jobs without duplicating on URL collisions (``save``).

Design choices (V1):

* **Connection-per-call.** Each method opens and closes its own
  ``sqlite3.Connection``. This keeps the class trivially thread-safe
  and removes the "did the caller close it?" footgun. SQLite open cost
  is negligible for our scale.
* **Implicit init.** ``has_seen`` and ``save`` lazily call
  ``init_db`` on first use via an internal flag, so a caller that
  forgets to call ``init_db`` does not crash. ``init_db`` is still
  public so callers can pre-warm and surface filesystem errors
  during boot.
* **``INSERT OR IGNORE`` on URL.** ``url`` is the natural dedup key;
  re-saving a job with the same URL is a no-op rather than an error.
  This is what lets a re-run of the monitor see the same job twice
  without raising.
* **JSON for keyword lists.** ``matched_keywords`` and
  ``excluded_keywords`` are stored as JSON-encoded text. SQLite has
  no native list type and a normalized join table is overkill for V1;
  JSON keeps reads round-trippable in a single ``SELECT``.
* **No ORM.** Plain ``sqlite3`` keeps the dependency surface tiny and
  the schema obvious. If the persistence concern grows we can swap
  in SQLAlchemy later without changing the public API.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.models.job import Job
from app.models.scored_job import ScoredJob


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    work_type TEXT,
    seniority TEXT,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    matched_keywords TEXT,
    excluded_keywords TEXT,
    relevant INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL
);
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO jobs (
    title, company, location, work_type, seniority, source, url,
    description, published_at, discovered_at, score,
    matched_keywords, excluded_keywords, relevant, first_seen_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_HAS_SEEN_SQL = "SELECT 1 FROM jobs WHERE url = ? LIMIT 1;"


class JobRepository:
    """Persist and look up ``ScoredJob``s in a local SQLite database."""

    def __init__(self, db_path: str = "data/jobs.db") -> None:
        """Store the target database path.

        The file itself is created lazily on first ``init_db`` /
        ``save`` / ``has_seen`` call. No I/O happens here so the
        constructor is safe to invoke before the data directory
        exists.
        """
        self._db_path = db_path
        self._initialized: bool = False

    @property
    def db_path(self) -> str:
        """The SQLite file path this repository reads / writes."""
        return self._db_path

    def _ensure_init(self) -> None:
        """Run ``init_db`` if it has not run yet on this instance."""
        if not self._initialized:
            self.init_db()

    def init_db(self) -> None:
        """Create the parent directory and the ``jobs`` table if missing.

        Idempotent: safe to call multiple times. ``CREATE TABLE IF NOT
        EXISTS`` makes the schema step a no-op on warm starts; the
        ``mkdir`` call uses ``exist_ok=True`` so concurrent processes
        racing to create the directory do not raise.
        """
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_SCHEMA_SQL)
            conn.commit()

        self._initialized = True

    def has_seen(self, job: Job) -> bool:
        """Return ``True`` iff a row with ``job.url`` already exists.

        URL is the natural identity of a job posting (title / company
        can drift across reposts, but the canonical URL does not).
        """
        self._ensure_init()
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(_HAS_SEEN_SQL, (job.url,))
            return cursor.fetchone() is not None

    def save(self, scored_job: ScoredJob) -> None:
        """Persist ``scored_job`` unless its URL was already recorded.

        Keyword lists are JSON-encoded; ``relevant`` is stored as 0/1.
        ``first_seen_at`` is set to the UTC moment of the save call,
        so re-saves of an already-known URL do not overwrite the
        original timestamp (``INSERT OR IGNORE`` is a no-op there).
        """
        self._ensure_init()

        job = scored_job.job
        first_seen_at = datetime.utcnow().isoformat()

        params = (
            job.title,
            job.company,
            job.location,
            job.work_type,
            job.seniority,
            job.source,
            job.url,
            job.description,
            job.published_at,
            job.discovered_at,
            scored_job.score,
            json.dumps(scored_job.matched_keywords),
            json.dumps(scored_job.excluded_keywords),
            1 if scored_job.relevant else 0,
            first_seen_at,
        )

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_INSERT_SQL, params)
            conn.commit()


__all__ = ["JobRepository"]
