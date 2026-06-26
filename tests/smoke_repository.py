"""Smoke test for JobRepository (SQLite persistence layer).

Run with ``python tests/smoke_repository.py`` from the project root.
Prints ``<NAME>_OK ...`` lines on success and exits 0. On any failure
prints ``<NAME>_FAIL ...`` with the offending value, dumps the failure
list, and exits 1.

Each run uses a fresh temporary SQLite file so the test is hermetic
and does not touch ``data/jobs.db``.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

failures: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"{name}_OK {detail}".rstrip())
    else:
        print(f"{name}_FAIL {detail}")
        failures.append(name)


# ------------------------------- helpers ---------------------------------------


class _TempDB:
    """Context manager that gives an isolated SQLite path per test block."""

    def __enter__(self) -> str:
        # ignore_cleanup_errors=True swallows the (benign) Windows race
        # where the SQLite file handle is still being released by the OS
        # when the tempdir teardown runs. We additionally wrap cleanup in
        # a broad except because some Python 3.11.x builds still let
        # PermissionError through. Either way the test logic itself has
        # already completed by the time we get here, so a leftover temp
        # dir is harmless — the OS temp cleaner reaps it later.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        # Place the db in a 'data/' subdir so the parent-mkdir branch is
        # also exercised — matches the real 'data/jobs.db' layout.
        data_dir = Path(self._tmp.name) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / "jobs.db")

    def __exit__(self, *exc) -> None:
        try:
            self._tmp.cleanup()
        except OSError:
            # Benign: OS still holding the SQLite handle. The test has
            # already produced its verdict; let the temp cleaner reap it.
            pass


def _build_scorer():
    from app.filters.job_scorer import JobScorer

    return JobScorer(
        include_keywords=[
            "java", "spring", "backend", "sql", "rest", "junior",
            "new graduate", "application support", "erp", "integration",
        ],
        exclude_keywords=["senior", "lead", "5+ years"],
        minimum_score=50,
    )


def _count_rows(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM jobs;")
        return int(cursor.fetchone()[0])


def _select_row(db_path: str, url: str) -> tuple | None:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT score, matched_keywords, excluded_keywords, relevant "
            "FROM jobs WHERE url = ?;",
            (url,),
        )
        return cursor.fetchone()


# ------------------------------- tests -----------------------------------------


def _check_parse() -> None:
    try:
        import app.database.job_repository  # noqa: F401
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_init_db_creates_table() -> None:
    from app.database.job_repository import JobRepository

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        repo.init_db()

        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs';"
            )
            row = cursor.fetchone()

        if row is None or row[0] != "jobs":
            _record("INIT_TABLE", False, f"row={row}")
            return

        # Init must be idempotent — calling again does not raise.
        try:
            repo.init_db()
        except Exception as exc:
            _record("INIT_IDEMPOTENT", False, repr(exc))
            return

        _record("INIT_TABLE", True, "jobs table created, init idempotent")


def _check_init_creates_data_dir() -> None:
    """init_db must create the parent directory if missing."""
    from app.database.job_repository import JobRepository

    outer = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    try:
        # Point at a non-existent nested directory; init_db should mkdir.
        db_path = str(Path(outer.name) / "nested" / "sub" / "jobs.db")
        repo = JobRepository(db_path=db_path)
        repo.init_db()

        if not Path(db_path).exists():
            _record("INIT_MKDIR", False, f"db file not created at {db_path}")
            return
        _record("INIT_MKDIR", True, "nested data dir created")
    finally:
        try:
            outer.cleanup()
        except OSError:
            pass


def _check_save_and_has_seen_round_trip() -> None:
    from app.database.job_repository import JobRepository
    from app.sources.dummy_source import DummySource

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        scorer = _build_scorer()
        jobs = DummySource().fetch_jobs()
        scored_jobs = [scorer.score(j) for j in jobs]
        junior_scored = next(s for s in scored_jobs if s.job.title.startswith("Junior"))

        if repo.has_seen(junior_scored.job):
            _record("HAS_SEEN_BEFORE_SAVE", False, "expected False before first save")
            return

        repo.save(junior_scored)

        if not repo.has_seen(junior_scored.job):
            _record("HAS_SEEN_AFTER_SAVE", False, "expected True after save")
            return

        if _count_rows(db_path) != 1:
            _record("ROW_COUNT_AFTER_SAVE", False, f"expected 1, got {_count_rows(db_path)}")
            return

        _record("SAVE_HAS_SEEN", True, "Junior saved, has_seen True, 1 row")


def _check_duplicate_save_is_noop() -> None:
    """Re-saving the same URL must not raise and must not duplicate."""
    from app.database.job_repository import JobRepository
    from app.sources.dummy_source import DummySource

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        scorer = _build_scorer()
        scored_jobs = [scorer.score(j) for j in DummySource().fetch_jobs()]
        junior_scored = next(s for s in scored_jobs if s.job.title.startswith("Junior"))

        repo.save(junior_scored)
        try:
            repo.save(junior_scored)
            repo.save(junior_scored)  # third time, still fine
        except Exception as exc:
            _record("DUP_SAVE_NO_RAISE", False, repr(exc))
            return

        if _count_rows(db_path) != 1:
            _record("DUP_SAVE_UNIQUE", False, f"expected 1, got {_count_rows(db_path)}")
            return

        _record("DUP_SAVE", True, "duplicate saves are silent, row count stays 1")


def _check_unseen_url_is_false() -> None:
    """A URL that was never saved must report has_seen == False."""
    from app.database.job_repository import JobRepository
    from app.models.job import Job

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        repo.init_db()

        stranger = Job(
            title="Stranger Job",
            company="X",
            location=None,
            work_type=None,
            seniority=None,
            source="dummy",
            url="https://example.com/never-saved",
            description=None,
            published_at=None,
            discovered_at="2026-06-26T00:00:00",
        )

        if repo.has_seen(stranger):
            _record("UNSEEN_FALSE", False, "fresh URL should not be marked seen")
            return
        _record("UNSEEN", True, "fresh URL has_seen=False")


def _check_keywords_stored_as_json() -> None:
    """matched_keywords / excluded_keywords round-trip via JSON."""
    from app.database.job_repository import JobRepository
    from app.sources.dummy_source import DummySource

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        scorer = _build_scorer()
        scored_jobs = [scorer.score(j) for j in DummySource().fetch_jobs()]
        junior_scored = next(s for s in scored_jobs if s.job.title.startswith("Junior"))

        repo.save(junior_scored)

        row = _select_row(db_path, junior_scored.job.url)
        if row is None:
            _record("JSON_ROW_MISSING", False, "row not found after save")
            return

        score, matched_json, excluded_json, relevant = row
        if score != junior_scored.score:
            _record("JSON_SCORE", False, f"db={score} expected={junior_scored.score}")
            return

        try:
            matched = json.loads(matched_json)
            excluded = json.loads(excluded_json)
        except json.JSONDecodeError as exc:
            _record("JSON_PARSE", False, repr(exc))
            return

        if matched != junior_scored.matched_keywords:
            _record(
                "JSON_MATCHED",
                False,
                f"db={matched} expected={junior_scored.matched_keywords}",
            )
            return
        if excluded != junior_scored.excluded_keywords:
            _record(
                "JSON_EXCLUDED",
                False,
                f"db={excluded} expected={junior_scored.excluded_keywords}",
            )
            return
        if relevant != 1:
            _record("RELEVANT_FLAG", False, f"expected 1, got {relevant}")
            return

        _record(
            "JSON_KEYWORDS",
            True,
            f"matched={matched} relevant=1",
        )


def _check_end_to_end_pipeline() -> None:
    """DummySource + JobScorer + repo: only relevant jobs persisted."""
    from app.database.job_repository import JobRepository
    from app.sources.dummy_source import DummySource

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        scorer = _build_scorer()
        scored_jobs = [scorer.score(j) for j in DummySource().fetch_jobs()]

        for s in scored_jobs:
            if s.relevant:
                repo.save(s)

        titles_in_db = []
        with sqlite3.connect(db_path) as conn:
            for (title,) in conn.execute("SELECT title FROM jobs ORDER BY id;"):
                titles_in_db.append(title)

        if "Junior Java Backend Developer" not in titles_in_db:
            _record("PIPELINE_JUNIOR", False, f"junior missing: {titles_in_db}")
            return
        if "Application Support Specialist (SQL)" not in titles_in_db:
            _record("PIPELINE_APPSUP", False, f"appsup missing: {titles_in_db}")
            return
        if "Senior Backend Lead" in titles_in_db:
            _record("PIPELINE_SENIOR", False, f"senior leaked into db: {titles_in_db}")
            return
        if len(titles_in_db) != 2:
            _record(
                "PIPELINE_COUNT",
                False,
                f"expected 2 rows, got {len(titles_in_db)}: {titles_in_db}",
            )
            return

        _record("PIPELINE", True, f"2 relevant jobs persisted, senior filtered out")


def _check_implicit_init_on_first_save() -> None:
    """Saving without an explicit init_db must still work."""
    from app.database.job_repository import JobRepository
    from app.sources.dummy_source import DummySource

    with _TempDB() as db_path:
        repo = JobRepository(db_path=db_path)
        # No init_db call here — save should self-initialize.
        scorer = _build_scorer()
        scored_jobs = [scorer.score(j) for j in DummySource().fetch_jobs()]
        junior_scored = next(s for s in scored_jobs if s.job.title.startswith("Junior"))

        try:
            repo.save(junior_scored)
        except Exception as exc:
            _record("IMPLICIT_INIT", False, repr(exc))
            return

        if not repo.has_seen(junior_scored.job):
            _record(
                "IMPLICIT_INIT_HAS_SEEN",
                False,
                "row should be queryable after implicit-init save",
            )
            return
        _record("IMPLICIT_INIT", True, "save() lazy-initialized schema")


def main() -> int:
    _check_parse()
    _check_init_db_creates_table()
    _check_init_creates_data_dir()
    _check_save_and_has_seen_round_trip()
    _check_duplicate_save_is_noop()
    _check_unseen_url_is_false()
    _check_keywords_stored_as_json()
    _check_end_to_end_pipeline()
    _check_implicit_init_on_first_save()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_REPOSITORY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
