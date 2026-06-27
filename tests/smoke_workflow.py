"""Smoke test for the V1 workflow: Job / ScoredJob / scorer / service / main.

Run with ``python tests/smoke_workflow.py`` from the project root.
Prints ``<NAME>_OK ...`` lines on success and exits 0. On any failure
prints ``<NAME>_FAIL ...`` with the offending value, dumps the failure
list, and exits 1 so the run is CI-friendly.

These are smoke tests, not unit tests: they exercise the public surface
end-to-end (import + behavior + main.py subprocess) without mocking.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
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


# ------------------------------- imports / parse -------------------------------


def _check_parse() -> None:
    try:
        import app.models.job  # noqa: F401
        import app.models.scored_job  # noqa: F401
        import app.filters.job_scorer  # noqa: F401
        import app.services.job_monitor_service  # noqa: F401
        import app.sources.base_source  # noqa: F401
        import app.sources.dummy_source  # noqa: F401
        import main  # noqa: F401
    except Exception as exc:  # pragma: no cover - smoke visibility
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


# ------------------------------- model shape -----------------------------------


def _check_job_no_scoring_fields() -> None:
    """Job must NOT carry score / matched / excluded; it is the raw record."""
    from app.models.job import Job

    fields = {f.name for f in dataclasses.fields(Job)}
    forbidden = {"score", "matched_keywords", "excluded_keywords"}
    leaked = fields & forbidden
    if leaked:
        _record(
            "JOB_RAW",
            False,
            f"Job leaked scoring fields: {sorted(leaked)}",
        )
        return

    expected_required = {
        "title",
        "company",
        "location",
        "work_type",
        "seniority",
        "source",
        "url",
        "description",
        "published_at",
        "discovered_at",
    }
    missing = expected_required - fields
    if missing:
        _record("JOB_RAW", False, f"Job missing fields: {sorted(missing)}")
        return
    _record("JOB_RAW", True, f"{len(fields)} fields, no scoring state")


def _check_scored_job_shape() -> None:
    """ScoredJob must wrap a Job and carry the scoring decision."""
    from app.models.job import Job
    from app.models.scored_job import ScoredJob

    fields = {f.name for f in dataclasses.fields(ScoredJob)}
    expected = {"job", "score", "matched_keywords", "excluded_keywords", "relevant"}
    if fields != expected:
        _record(
            "SCORED_JOB_SHAPE",
            False,
            f"expected={sorted(expected)} got={sorted(fields)}",
        )
        return

    job = Job(
        title="t",
        company="c",
        location=None,
        work_type=None,
        seniority=None,
        source="dummy",
        url="https://example.com",
        description=None,
        published_at=None,
        discovered_at="2026-06-26T00:00:00",
    )
    scored = ScoredJob(job=job, score=10)
    if scored.relevant is not False:
        _record("SCORED_JOB_DEFAULT", False, "relevant should default False")
        return
    if scored.matched_keywords != [] or scored.excluded_keywords != []:
        _record("SCORED_JOB_DEFAULTS", False, "list defaults not empty")
        return
    if scored.job is not job:
        _record("SCORED_JOB_WRAP", False, "job ref should be the same object")
        return
    _record("SCORED_JOB", True, "5 fields, defaults sane, wraps Job")


# ------------------------------- normalize_text -------------------------------


def _check_normalize() -> None:
    from app.filters.job_text import normalize_text

    cases: list[tuple[object, str]] = [
        (None, ""),
        ("", ""),
        ("  JAVA  Spring  ", "java spring"),
        ("Yazılım Mühendisi", "yazilim muhendisi"),
        ("Mixed\tWhitespace\nLines", "mixed whitespace lines"),
        ("already lower", "already lower"),
    ]
    for given, expected in cases:
        actual = normalize_text(given if isinstance(given, str) else None)  # type: ignore[arg-type]
        if actual != expected:
            _record(
                "NORMALIZE",
                False,
                f"input={given!r} expected={expected!r} got={actual!r}",
            )
            return
    _record("NORMALIZE", True, f"{len(cases)} cases")


# ------------------------------- helpers ---------------------------------------


def _make_job(**overrides):
    from app.models.job import Job

    base = dict(
        title="placeholder",
        company="placeholder-co",
        location=None,
        work_type=None,
        seniority=None,
        source="dummy",
        url="https://example.com/x",
        description=None,
        published_at=None,
        discovered_at="2026-06-26T00:00:00",
    )
    base.update(overrides)
    return Job(**base)


def _build_scorer(include, exclude, threshold=50, include_weight=20, exclude_weight=40):
    from app.filters.job_scorer import JobScorer

    return JobScorer(
        include_keywords=include,
        exclude_keywords=exclude,
        minimum_score=threshold,
        include_weight=include_weight,
        exclude_weight=exclude_weight,
        domain_required_keywords=include,
    )


# ------------------------------- scorer behavior -------------------------------


def _check_score_returns_scored_job() -> None:
    """scorer.score(job) must return a ScoredJob (not mutate Job)."""
    from app.models.scored_job import ScoredJob

    job = _make_job(title="Java Developer")
    scorer = _build_scorer(include=["java"], exclude=[])
    out = scorer.score(job)
    if not isinstance(out, ScoredJob):
        _record("SCORE_RETURNS_SCORED", False, f"got {type(out).__name__}")
        return
    if out.job is not job:
        _record("SCORE_WRAPS_INPUT", False, "ScoredJob.job should be the input")
        return
    _record("SCORE_RETURNS_SCORED", True, "returns ScoredJob wrapping input")


def _check_junior_java() -> None:
    job = _make_job(
        title="Junior Java Backend Developer",
        company="SpringyCorp",
        location="Istanbul",
        work_type="Hybrid",
        seniority="Junior",
        description="Java, Spring Boot, REST, SQL — new graduate friendly.",
    )
    scorer = _build_scorer(
        include=["java", "spring", "backend", "sql", "rest", "junior", "new graduate"],
        exclude=["senior", "lead", "5+ years"],
    )
    scored = scorer.score(job)

    if scored.score != 7 * 20:
        _record("JUNIOR_SCORE", False, f"expected 140, got {scored.score}")
        return
    if len(scored.matched_keywords) != 7:
        _record(
            "JUNIOR_MATCHED",
            False,
            f"expected 7 matched, got {scored.matched_keywords}",
        )
        return
    if scored.excluded_keywords:
        _record(
            "JUNIOR_EXCLUDED",
            False,
            f"expected no excludes, got {scored.excluded_keywords}",
        )
        return
    if scored.relevant is not True:
        _record("JUNIOR_RELEVANT", False, f"expected relevant=True, got {scored.relevant}")
        return
    _record("JUNIOR", True, f"score={scored.score} relevant={scored.relevant}")


def _check_senior_lead() -> None:
    job = _make_job(
        title="Senior Backend Lead",
        company="MegaScale Inc.",
        location="Remote",
        work_type="Remote",
        seniority="Senior",
        description="Senior/Lead role, 5+ years, architecture ownership.",
    )
    scorer = _build_scorer(
        include=["java", "spring", "backend", "sql", "rest", "junior"],
        exclude=["senior", "lead", "5+ years"],
    )
    scored = scorer.score(job)

    if scored.score != 20 - 120:
        _record("SENIOR_SCORE", False, f"expected -100, got {scored.score}")
        return
    if scored.relevant is not False:
        _record("SENIOR_RELEVANT", False, f"expected relevant=False, got {scored.relevant}")
        return
    _record("SENIOR", True, f"score={scored.score} relevant={scored.relevant}")


def _check_app_support() -> None:
    job = _make_job(
        title="Application Support Specialist (SQL)",
        company="ERPify",
        location="Ankara",
        work_type="On-site",
        seniority="Mid",
        description="Application support for ERP system, SQL, integration tickets.",
    )
    scorer = _build_scorer(
        include=["application support", "sql", "erp", "integration", "junior"],
        exclude=["senior", "lead"],
    )
    scored = scorer.score(job)

    if scored.score != 4 * 20:
        _record("APPSUP_SCORE", False, f"expected 80, got {scored.score}")
        return
    if scored.relevant is not True:
        _record("APPSUP_RELEVANT", False, f"expected relevant=True, got {scored.relevant}")
        return
    _record("APPSUP", True, f"score={scored.score} matched={scored.matched_keywords}")


def _check_no_mutation() -> None:
    """Scorer must return a new ScoredJob; the input Job stays untouched."""
    job = _make_job(
        title="Junior Java",
        description="java backend",
        company="X",
    )
    snapshot = {
        "title": job.title,
        "company": job.company,
        "description": job.description,
        "url": job.url,
        "discovered_at": job.discovered_at,
    }

    scorer = _build_scorer(include=["java"], exclude=[])
    scored = scorer.score(job)

    if scored.job is job and scored.score != 20:
        # Sanity: scoring should have produced the expected 20 for 'java'.
        _record(
            "IMMUTABLE_SANITY",
            False,
            f"unexpected score {scored.score} for 'java' on this job",
        )
        return

    for field_name, expected in snapshot.items():
        actual = getattr(job, field_name)
        if actual != expected:
            _record(
                "IMMUTABLE",
                False,
                f"Job.{field_name} mutated: {actual!r} != {expected!r}",
            )
            return
    _record("IMMUTABLE", True, "input Job identity fields untouched")


def _check_dedup_keyword() -> None:
    job = _make_job(
        title="Java Java Java Java",
        description="java java junior junior backend",
    )
    scorer = _build_scorer(include=["java", "junior", "backend"], exclude=[])
    scored = scorer.score(job)

    if scored.score != 60:
        _record(
            "DEDUP_SCORE",
            False,
            f"expected 60 (each kw counted once), got {scored.score}",
        )
        return
    if scored.matched_keywords.count("java") != 1:
        _record(
            "DEDUP_MATCHED",
            False,
            f"java should appear once in matched list: {scored.matched_keywords}",
        )
        return
    _record("DEDUP", True, f"matched={scored.matched_keywords} score={scored.score}")


def _check_weights_applied() -> None:
    """include_weight / exclude_weight from constructor change the score."""
    job = _make_job(title="Senior Java Engineer", description="lead architect")
    # 1 exclude hit (-lead) and 1 include hit (+java).
    default_scorer = _build_scorer(include=["java"], exclude=["lead"])
    custom_scorer = _build_scorer(
        include=["java"],
        exclude=["lead"],
        include_weight=10,
        exclude_weight=5,
    )
    default_scored = default_scorer.score(job)
    custom_scored = custom_scorer.score(job)

    if default_scored.score != 20 - 40:
        _record(
            "WEIGHT_DEFAULT",
            False,
            f"expected -20 (20-40), got {default_scored.score}",
        )
        return
    if custom_scored.score != 10 - 5:
        _record(
            "WEIGHT_CUSTOM",
            False,
            f"expected 5 (10-5), got {custom_scored.score}",
        )
        return
    _record(
        "WEIGHTS",
        True,
        f"default={default_scored.score} custom={custom_scored.score}",
    )


def _check_threshold_edge() -> None:
    job = _make_job(title="Java Engineer")
    scorer_eq = _build_scorer(include=["java"], exclude=[], threshold=20)
    scored_eq = scorer_eq.score(job)
    if scored_eq.relevant is not True:
        _record("THRESHOLD_EQ", False, "score == min should be relevant=True")
        return
    scorer_below = _build_scorer(include=["java"], exclude=[], threshold=21)
    if scorer_below.score(job).relevant is not False:
        _record("THRESHOLD_BELOW", False, "score < min should be relevant=False")
        return
    _record("THRESHOLD", True, ">= boundary behaves correctly")


def _check_empty_keywords() -> None:
    job = _make_job(title="Anything Goes Here")
    scorer = _build_scorer(include=[], exclude=[], threshold=50)
    scored = scorer.score(job)
    if scored.score != 0 or scored.relevant is not False:
        _record(
            "EMPTY",
            False,
            f"expected score=0, relevant=False, got score={scored.score} relevant={scored.relevant}",
        )
        return
    _record("EMPTY", True, "no keywords -> score=0, irrelevant")


def _check_none_fields_safe() -> None:
    job = _make_job(
        title="Junior Backend",
        location=None,
        work_type=None,
        seniority=None,
        description=None,
    )
    # threshold=20 keeps the assertion focused on the "None-safe combine
    # didn't crash" contract, not on the threshold-vs-score semantics.
    scorer = _build_scorer(include=["junior", "backend"], exclude=[], threshold=20)
    scored = scorer.score(job)
    if scored.score != 40 or scored.relevant is not True:
        _record(
            "NONE_FIELDS",
            False,
            f"None fields broke scoring, got score={scored.score} relevant={scored.relevant}",
        )
        return
    _record("NONE_FIELDS", True, "None fields handled, score=40, relevant=True")


def _check_is_relevant_removed() -> None:
    """V1 contract: JobScorer has no is_relevant method (ScoredJob.relevant is the source)."""
    from app.filters.job_scorer import JobScorer

    if hasattr(JobScorer, "is_relevant"):
        _record(
            "IS_RELEVANT_REMOVED",
            False,
            "JobScorer.is_relevant still exists; V1 expects ScoredJob.relevant only",
        )
        return
    _record("IS_RELEVANT_REMOVED", True, "JobScorer.is_relevant not present")


# ------------------------------- JobMonitorService -----------------------------


class _FlakySource:
    """A source whose fetch_jobs raises — used to verify failure isolation."""

    name = "flaky"

    def fetch_jobs(self):  # noqa: D401 — test double
        raise RuntimeError("simulated network failure")


class _ExtraJobsSource:
    """A source that returns an additional relevant job beyond DummySource's 3."""

    name = "extra"

    def fetch_jobs(self):
        return [
            _make_job(
                title="Junior Backend (extra)",
                company="ExtraCorp",
                source="extra",
                url="https://example.com/extra",
            ),
        ]


def _check_service_filters_irrelevant() -> None:
    """Service returns only relevant ScoredJobs from DummySource."""
    from app.services.job_monitor_service import JobMonitorService
    from app.sources.dummy_source import DummySource

    scorer = _build_scorer(
        include=["java", "spring", "backend", "sql", "rest", "junior",
                 "new graduate", "application support", "erp", "integration"],
        exclude=["senior", "lead", "5+ years"],
        threshold=50,
    )
    service = JobMonitorService(sources=[DummySource()], scorer=scorer)
    relevant = service.run()

    titles = [s.job.title for s in relevant]
    if "Junior Java Backend Developer" not in titles:
        _record("SERVICE_KEEPS_JUNIOR", False, f"junior missing: {titles}")
        return
    if "Application Support Specialist (SQL)" not in titles:
        _record("SERVICE_KEEPS_APPSUP", False, f"appsup missing: {titles}")
        return
    if any(s.job.title == "Senior Backend Lead" for s in relevant):
        _record("SERVICE_DROPS_SENIOR", False, "senior leaked into relevant list")
        return
    if len(relevant) != 2:
        _record(
            "SERVICE_COUNT",
            False,
            f"expected 2 relevant (junior+appsup), got {len(relevant)}: {titles}",
        )
        return
    _record("SERVICE_FILTER", True, f"returned {len(relevant)} relevant")


def _check_service_isolates_source_failures() -> None:
    """A failing source must not stop the others from running."""
    from app.services.job_monitor_service import JobMonitorService

    scorer = _build_scorer(include=["java", "spring", "backend"], exclude=[], threshold=0)
    service = JobMonitorService(
        sources=[_FlakySource(), _ExtraJobsSource()],
        scorer=scorer,
    )
    relevant = service.run()

    if len(relevant) != 1:
        _record(
            "SERVICE_ISOLATION",
            False,
            f"expected 1 (only extra), got {len(relevant)}",
        )
        return
    if relevant[0].job.source != "extra":
        _record(
            "SERVICE_ISOLATION_SRC",
            False,
            f"expected source='extra', got {relevant[0].job.source}",
        )
        return
    _record("SERVICE_ISOLATION", True, "flaky source skipped, extra source ran")


def _check_service_returns_list_of_scored_jobs() -> None:
    """Service must not return raw Jobs, and must not include notifier/SQL."""
    from app.models.scored_job import ScoredJob
    from app.services.job_monitor_service import JobMonitorService
    from app.sources.dummy_source import DummySource

    scorer = _build_scorer(include=["java"], exclude=[], threshold=0)
    service = JobMonitorService(sources=[DummySource()], scorer=scorer)
    relevant = service.run()
    if not all(isinstance(s, ScoredJob) for s in relevant):
        _record("SERVICE_TYPE", False, "service returned non-ScoredJob entries")
        return
    _record("SERVICE_TYPE", True, f"{len(relevant)} ScoredJob entries")


# ------------------------------- JobMonitorService + repository ----------------


def _build_realistic_scorer():
    """A scorer that matches the same keywords config.yaml uses."""
    return _build_scorer(
        include=[
            "java", "spring", "backend", "sql", "rest", "junior",
            "new graduate", "application support", "erp", "integration",
        ],
        exclude=["senior", "lead", "5+ years"],
        threshold=50,
    )


def _make_temp_repo():
    """Return (db_path, JobRepository, tempdir_handle) — caller must cleanup."""
    import tempfile
    from app.database.job_repository import JobRepository

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    data_dir = Path(tmp.name) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(data_dir / "jobs.db")
    repo = JobRepository(db_path=db_path)
    repo.init_db()
    return tmp, db_path, repo


def _count_in_db(db_path: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM jobs;")
        return int(cursor.fetchone()[0])


def _check_service_repository_first_run() -> None:
    """First run with a fresh repository: 2 new relevant jobs persisted."""
    from app.services.job_monitor_service import JobMonitorService
    from app.sources.dummy_source import DummySource

    tmp, db_path, repo = _make_temp_repo()
    try:
        service = JobMonitorService(
            sources=[DummySource()],
            scorer=_build_realistic_scorer(),
            repository=repo,
        )
        new_relevant = service.run()

        if len(new_relevant) != 2:
            _record(
                "REPO_FIRST_RUN_COUNT",
                False,
                f"expected 2 new relevant, got {len(new_relevant)}",
            )
            return
        if _count_in_db(db_path) != 2:
            _record(
                "REPO_FIRST_DB_COUNT",
                False,
                f"expected 2 rows in db, got {_count_in_db(db_path)}",
            )
            return
        _record(
            "REPO_FIRST_RUN",
            True,
            f"{len(new_relevant)} new relevant, db has 2 rows",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_service_repository_second_run_dedups() -> None:
    """Second run with same repository: 0 new, db count unchanged."""
    from app.services.job_monitor_service import JobMonitorService
    from app.sources.dummy_source import DummySource

    tmp, db_path, repo = _make_temp_repo()
    try:
        scorer = _build_realistic_scorer()
        service = JobMonitorService(
            sources=[DummySource()],
            scorer=scorer,
            repository=repo,
        )

        # First run populates the repo.
        first = service.run()
        if len(first) != 2:
            _record(
                "REPO_DEDUP_PRECONDITION",
                False,
                f"first run should yield 2, got {len(first)}",
            )
            return

        # Second run with same repo — DummySource returns identical URLs,
        # so both jobs must be filtered out by has_seen.
        second_service = JobMonitorService(
            sources=[DummySource()],
            scorer=scorer,
            repository=repo,
        )
        second = second_service.run()

        if len(second) != 0:
            _record(
                "REPO_DEDUP_SECOND_RUN",
                False,
                f"expected 0 new on second run, got {len(second)}",
            )
            return
        if _count_in_db(db_path) != 2:
            _record(
                "REPO_DEDUP_DB_COUNT",
                False,
                f"db count drifted to {_count_in_db(db_path)}, expected 2",
            )
            return
        _record(
            "REPO_DEDUP",
            True,
            "second run returned 0 new, db count unchanged at 2",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_service_repository_skips_senior() -> None:
    """Senior job is irrelevant → not persisted, not in returned list."""
    import sqlite3
    from app.services.job_monitor_service import JobMonitorService
    from app.sources.dummy_source import DummySource

    tmp, db_path, repo = _make_temp_repo()
    try:
        service = JobMonitorService(
            sources=[DummySource()],
            scorer=_build_realistic_scorer(),
            repository=repo,
        )
        service.run()

        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT title FROM jobs WHERE title LIKE '%Senior%';"
            )
            senior_rows = cursor.fetchall()

        if senior_rows:
            _record(
                "REPO_NO_SENIOR",
                False,
                f"senior leaked into db: {senior_rows}",
            )
            return
        _record("REPO_NO_SENIOR", True, "senior job not persisted")
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


# ------------------------------- main.py subprocess ----------------------------


def _check_main_subprocess() -> None:
    """Run main.py end-to-end and verify its logs.

    The repository contract here is "fresh DB → 2 new relevant
    postings", which requires the real ``data/jobs.db`` to be
    absent. To avoid clobbering the user's real database, we
    rename it aside if present, run main.py in the project root
    cwd (so ``app/config/config.yaml`` resolves correctly), and
    restore the original file in the finally block.

    The second-run dedup behavior is covered separately by the
    ``REPO_DEDUP`` service-level test, which uses an isolated
    tempdir — so this test stays focused on the happy path.
    """
    db_path = PROJECT_ROOT / "data" / "jobs.db"
    backup_path = PROJECT_ROOT / "data" / "jobs.db.smokebak"
    had_existing = db_path.exists()
    if had_existing:
        db_path.rename(backup_path)

    try:
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "main.py")],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        # Whatever happened, put the original db back where it was.
        # main.py may have created a fresh jobs.db during the run;
        # remove that before restoring the backup so we never
        # silently overwrite the user's real data.
        if db_path.exists():
            try:
                db_path.unlink()
            except OSError:
                pass
        if had_existing and backup_path.exists():
            backup_path.rename(db_path)

    if proc.returncode != 0:
        _record(
            "MAIN_EXIT",
            False,
            f"exit={proc.returncode} stderr={proc.stderr.strip()!r} "
            f"stdout_tail={proc.stdout.strip()[-400:]!r}",
        )
        return

    expected_tokens = [
        "Manul Sentinel starting",
        "Config loaded successfully",
        "Repository ready at data/jobs.db",
        "JobMonitorService starting with 1 source(s)",
        "Source 'dummy' returned 3 job(s)",
        "JobMonitorService completed: 2 new relevant out of 3 total",
        "Workflow produced 2 new relevant job(s)",
        "Junior Java Backend Developer",
        "Application Support Specialist",
        "Monitoring workflow completed",
    ]
    combined = proc.stdout + "\n" + proc.stderr
    missing = [t for t in expected_tokens if t not in combined]
    if missing:
        _record("MAIN_LOGS", False, f"missing log tokens: {missing}")
        return

    relevant_block_started = False
    relevant_block_lines: list[str] = []
    for ln in combined.splitlines():
        if "Workflow produced" in ln:
            relevant_block_started = True
            continue
        if "Monitoring workflow completed" in ln:
            relevant_block_started = False
            continue
        if relevant_block_started and ln.strip():
            relevant_block_lines.append(ln)
    senior_in_relevant = any(
        "Senior Backend Lead" in ln for ln in relevant_block_lines
    )
    if senior_in_relevant:
        _record(
            "MAIN_SENIOR_FILTERED",
            False,
            "senior appeared in the relevant-job log block",
        )
        return

    _record("MAIN", True, "exit=0, all expected tokens present, senior filtered")


def main() -> int:
    _check_parse()
    _check_job_no_scoring_fields()
    _check_scored_job_shape()
    _check_normalize()
    _check_score_returns_scored_job()
    _check_junior_java()
    _check_senior_lead()
    _check_app_support()
    _check_no_mutation()
    _check_dedup_keyword()
    _check_weights_applied()
    _check_threshold_edge()
    _check_empty_keywords()
    _check_none_fields_safe()
    _check_is_relevant_removed()
    _check_service_filters_irrelevant()
    _check_service_isolates_source_failures()
    _check_service_returns_list_of_scored_jobs()
    _check_service_repository_first_run()
    _check_service_repository_second_run_dedups()
    _check_service_repository_skips_senior()
    _check_main_subprocess()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_WORKFLOW_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
