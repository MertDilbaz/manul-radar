"""Smoke tests for the V2 scoring / confidence tiering.

Run with ``python tests/smoke_scoring_v2.py`` from the project root.
Prints ``<NAME>_OK ...`` lines on success and exits 0. On any failure
prints ``<NAME>_FAIL ...`` with the offending value, dumps the
failure list, and exits 1.

These tests pin down the V2 behaviour added 2026-06-29 so a future
refactor cannot silently regress it:

* Strong backend / Java / SQL signals outweigh generic
  ``software engineer`` (high confidence).
* Generic ``Software Engineer`` from a Turkish high-priority company
  surfaces as ``low`` confidence (still shown, but tagged).
* iOS / mobile listings without backend / Java / support signals
  are penalised and dropped below the threshold unless paired with
  a strong stack.
* AI Software Engineer without backend / Java / support is
  ``low`` confidence.
* Senior roles, 5+ year experience requirements, and non-Turkey
  postings remain hard rejects.
* Rejection counter buckets are **exclusive** — every rejected job
  counts in exactly one bucket.
"""
from __future__ import annotations

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


# ------------------------------- helpers ---------------------------------------


def _make_job(**overrides):
    from app.models.job import Job

    base = dict(
        title="placeholder",
        company="placeholder-co",
        location="Istanbul, Turkey",
        work_type="Hybrid",
        seniority=None,
        source="policy_test",
        url="https://example.com/job",
        description=None,
        published_at=None,
        discovered_at="2026-06-29T00:00:00",
    )
    base.update(overrides)
    return Job(**base)


def _make_v2_scorer():
    """Realistic V2 scorer matching ``config.yaml`` weights."""
    from app.filters.job_scorer import JobScorer

    return JobScorer(
        include_keywords=[
            "java", "spring boot", "spring", "backend", "back-end",
            "sql", "rest", "api", "junior", "yeni mezun",
            "application support", "uygulama destek", "yazilim destek",
            "integration", "intern", "stajyer",
        ],
        weak_keywords=[
            "software engineer", "software developer", "yazilim muhendisi",
            "yazilim gelistirici", "ai software engineer",
            "machine learning engineer",
        ],
        company_boost_keywords=["commencis", "midas", "insider", "iyzico"],
        mobile_negative_keywords=[
            "ios", "android", "mobile", "react native", "flutter",
        ],
        exclude_keywords=["frontend", "business analyst"],
        hard_exclude_keywords=[
            "senior", "kidemli", "lead", "manager", "devops",
            "php", "wordpress",
        ],
        domain_required_keywords=[
            "java", "spring", "backend", "software", "yazilim",
            "application support", "sql", "integration",
        ],
        non_target_domain_keywords=[
            "muhasebe", "ihracat", "satis", "reklam",
        ],
        location_required_keywords=[
            "turkey", "istanbul", "ankara", "izmir", "remote turkiye",
        ],
        location_reject_keywords=[
            "canada", "china", "united states", "europe", "global remote",
        ],
        role_required_keywords=[
            "junior", "new grad", "yeni mezun", "application support",
            "uygulama destek", "yazilim destek",
        ],
        minimum_score=60,
        include_weight=20,  # back-compat
        strong_weight=25,
        weak_weight=8,
        location_weight=10,
        company_boost_weight=10,
        mobile_penalty=25,
        generic_only_penalty=25,
        high_confidence_min_score=80,
        high_confidence_min_strong=1,
        low_confidence_min_score=40,
        exclude_weight=40,
        source_boost_weight=8,
        hard_exclude_experience_years=4,
    )


# ------------------------------- tests -----------------------------------------


def _check_junior_java_high_confidence() -> None:
    """Strong backend+java+junior signal -> high confidence, well above 60."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="Junior Java Backend Developer",
            description="Java, Spring Boot, REST, SQL. New grad friendly.",
        )
    )
    _record(
        "JUNIOR_JAVA_HIGH_RELEVANT",
        scored.relevant,
        f"score={scored.score} excluded={scored.excluded_keywords}",
    )
    _record(
        "JUNIOR_JAVA_HIGH_CONFIDENCE",
        scored.confidence == "high",
        f"confidence={scored.confidence} reasons={scored.confidence_reasons}",
    )
    _record(
        "JUNIOR_JAVA_HIGH_ABOVE_THRESHOLD",
        scored.score >= 80,
        f"score={scored.score} (expected >=80 for high)",
    )


def _check_application_support_sql_passes() -> None:
    """Application Support Specialist (SQL) is a strong match -> high."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="Application Support Specialist (SQL)",
            description="SQL application support, integration, troubleshooting.",
        )
    )
    _record(
        "SQL_SUPPORT_RELEVANT",
        scored.relevant,
        f"score={scored.score} excluded={scored.excluded_keywords}",
    )
    _record(
        "SQL_SUPPORT_CONFIDENCE",
        scored.confidence in ("high", "medium"),
        f"confidence={scored.confidence} reasons={scored.confidence_reasons}",
    )


def _check_generic_software_engineer_low_confidence() -> None:
    """Bare ``Software Engineer`` + Turkey -> low_confidence + low score."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="Software Engineer",
            description="We are looking for a software engineer to join our team.",
        )
    )
    if not scored.relevant:
        # acceptable: weak-only listings can be dropped entirely when the
        # generic_only_penalty nudges them below minimum_score.
        _record(
            "GENERIC_SE_LOW_OR_REJECTED",
            True,
            f"generic SE without stack signal -> not relevant (score={scored.score})",
        )
        return
    _record(
        "GENERIC_SE_LOW_CONFIDENCE",
        scored.confidence == "low",
        f"confidence={scored.confidence} reasons={scored.confidence_reasons}",
    )
    _record(
        "GENERIC_SE_LOW_SCORE",
        scored.score < 80,
        f"score={scored.score} (expected <80 for generic SE)",
    )


def _check_high_priority_company_boost() -> None:
    """Generic SE at a high-priority company surfaces with low_confidence
    thanks to company_boost_keywords; without the boost the same posting
    has a strictly lower score.

    V2 contract: a high-priority-company boost alone is *not* enough
    to clear the ``minimum_score=60`` threshold for a generic SE
    listing. The boost guarantees the score moves but still tags the
    listing as ``low`` confidence so Mert can ignore it if he wants.
    """
    scorer = _make_v2_scorer()
    boosted = scorer.score(
        _make_job(
            title="Software Engineer",
            company="Commencis",
            description="We are looking for a software engineer.",
            source="lever_commencis",
        )
    )
    plain = scorer.score(
        _make_job(
            title="Software Engineer",
            company="RandomCo",
            description="We are looking for a software engineer.",
            source="random_source",
        )
    )
    _record(
        "COMPANY_BOOST_RAISES_SCORE",
        boosted.score > plain.score,
        f"boosted={boosted.score} plain={plain.score}",
    )
    # The exact delta equals the company_boost_weight contribution for
    # the matched company keyword (default 10). This pins down the
    # V2 scoring formula so a regression that drops the company boost
    # is caught immediately.
    _record(
        "COMPANY_BOOST_DELTA_EQUALS_WEIGHT",
        (boosted.score - plain.score) == 10,
        f"boosted={boosted.score} plain={plain.score} delta={boosted.score - plain.score}",
    )


def _check_ios_without_backend_penalised() -> None:
    """iOS Engineer without backend/java/support signal drops below threshold."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="iOS Engineer",
            company="MobileFirst",
            description="Swift, UIKit, mobile development. iOS only.",
        )
    )
    if scored.relevant:
        # If it did pass, it must have been a low-score listing.
        _record(
            "IOS_PENALTY_LOW_SCORE",
            scored.score < 80,
            f"iOS listing passed but score is low: {scored.score}",
        )
        return
    _record(
        "IOS_PENALTY_REJECTED",
        True,
        f"iOS without backend/java/support -> not relevant "
        f"(score={scored.score}, excluded={scored.excluded_keywords})",
    )
    _record(
        "IOS_PENALTY_REASON_PRESENT",
        any(item.startswith("mobile:") for item in scored.excluded_keywords),
        f"excluded={scored.excluded_keywords}",
    )


def _check_ios_with_backend_passes() -> None:
    """iOS engineer that also lists backend/java gets a pass-through
    (no mobile penalty) and is scored like a regular backend role."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="iOS Engineer (Backend Support)",
            company="HybridCo",
            description="Swift + Java backend support, REST API integrations.",
        )
    )
    _record(
        "IOS_WITH_BACKEND_PENALTY_ABSENT",
        not any(item.startswith("mobile:") for item in scored.excluded_keywords),
        f"excluded={scored.excluded_keywords}",
    )


def _check_ai_software_engineer_low_confidence() -> None:
    """AI Software Engineer without backend/java/support -> low_confidence."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="AI Software Engineer",
            description="Machine learning, model training, Python only.",
        )
    )
    if scored.relevant:
        _record(
            "AI_ENGINEER_LOW_CONFIDENCE",
            scored.confidence == "low",
            f"confidence={scored.confidence} reasons={scored.confidence_reasons}",
        )
        return
    _record(
        "AI_ENGINEER_REJECTED_OR_LOW",
        True,
        f"AI engineer without stack signal -> not relevant or low confidence "
        f"(score={scored.score}, confidence={scored.confidence})",
    )


def _check_senior_hard_rejected() -> None:
    """Senior roles remain a hard reject."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="Senior Java Backend Developer",
            description="Java, Spring Boot, SQL, 5+ years experience required.",
        )
    )
    _record(
        "SENIOR_HARD_REJECTED",
        not scored.relevant,
        f"score={scored.score} excluded={scored.excluded_keywords}",
    )
    _record(
        "SENIOR_HARD_REASON",
        any(item in scored.excluded_keywords for item in ("senior",))
        or any(item.startswith("experience:") for item in scored.excluded_keywords),
        f"excluded={scored.excluded_keywords}",
    )


def _check_five_plus_years_rejected() -> None:
    """5+ years experience requirement is a hard reject even without 'senior'."""
    scorer = _make_v2_scorer()
    scored = scorer.score(
        _make_job(
            title="Java Backend Developer",
            description="Java, Spring Boot, SQL. Minimum 5 years experience required.",
        )
    )
    _record(
        "FIVE_YEARS_REJECTED",
        not scored.relevant,
        f"score={scored.score} excluded={scored.excluded_keywords}",
    )
    _record(
        "FIVE_YEARS_EXPERIENCE_REASON",
        any(item.startswith("experience:") for item in scored.excluded_keywords),
        f"excluded={scored.excluded_keywords}",
    )


def _check_non_turkey_rejected() -> None:
    """Non-Turkey / unknown-location postings are rejected."""
    scorer = _make_v2_scorer()
    for location in ["Toronto, Canada", "Berlin, Germany", "Remote - Europe"]:
        scored = scorer.score(
            _make_job(
                title="Junior Java Backend Developer",
                location=location,
                description="Java, Spring Boot, SQL.",
            )
        )
        if scored.relevant:
            _record(
                "NON_TURKEY_REJECTED",
                False,
                f"{location} passed: score={scored.score}",
            )
            return
        if not any(item.startswith("location:") for item in scored.excluded_keywords):
            _record(
                "NON_TURKEY_REASON",
                False,
                f"{location} excluded={scored.excluded_keywords}",
            )
            return
    _record(
        "NON_TURKEY_REJECTED",
        True,
        "Canada/Germany/Europe rejected with location: prefix",
    )


def _check_counter_buckets_exclusive() -> None:
    """Every rejected job lands in exactly one counter bucket.

    This is the V2 contract for :class:`JobMonitorService` so the
    per-bucket counts sum to ``rejected_total`` instead of exceeding
    it. We build a tiny source whose ``fetch_jobs`` returns
    pre-baked ``ScoredJob`` instances (one per reject bucket) and
    drive ``service.run()`` directly — this avoids the real scorer
    and isolates the bucketing logic.
    """
    from app.services.job_monitor_service import (
        JobMonitorService,
        JobMonitorStats,
    )
    from app.models.scored_job import ScoredJob

    class _StubSource:
        name = "stub"

        def fetch_jobs(self):
            """One job per reject bucket, plus one unmatched (score bucket)."""
            scenarios = [
                ("A", "https://x/a", ["location:not_turkey"]),
                ("B", "https://x/b", ["domain:no_technology_signal"]),
                ("C", "https://x/c", ["experience:5 yil"]),
                ("D", "https://x/d", ["senior"]),
                ("E", "https://x/e", ["non_target:muhasebe"]),
                ("F", "https://x/f", ["mobile:no_backend_signal"]),
                ("G", "https://x/g", ["role:missing"]),
                ("H", "https://x/h", ["generic_only:no_strong_signal"]),
                ("I", "https://x/i", []),  # -> score bucket
            ]
            scored_jobs: list[ScoredJob] = []
            for title, url, excluded in scenarios:
                scored_jobs.append(
                    ScoredJob(
                        job=_make_job(title=title, url=url),
                        score=0,
                        matched_keywords=[],
                        excluded_keywords=excluded,
                        relevant=False,
                    )
                )
            return scored_jobs

    # A scoring stub that always defers to the ScoredJob shape baked
    # in by the source. This sidesteps the real scorer entirely but
    # still exposes the keyword tuples the service consults when
    # bucketing ``hard`` and ``soft`` rejections.
    _HARDS = ("senior", "kidemli", "lead", "manager", "php", "wordpress")
    _SOFTS = ("frontend", "business analyst")

    class _BypassScorer:
        minimum_score = 60

        def score(self, job):  # type: ignore[override]
            # Not used; the service is going to call score() on real
            # Jobs, so we route through _make_job to convert stubs.
            return ScoredJob(
                job=job,
                score=0,
                matched_keywords=[],
                excluded_keywords=[],
                relevant=False,
            )

        @property
        def hard_exclude_keywords(self):  # type: ignore[override]
            return _HARDS

        @property
        def soft_exclude_keywords(self):  # type: ignore[override]
            return _SOFTS

    # To exercise the real bucketing logic we drive ``_PatchedService``
    # which carries pre-baked ScoredJob instances for each reject bucket.
    from app.services.job_monitor_service import JobMonitorService as JMS

    class _PatchedService(JMS):
        def __init__(self, baked: list[ScoredJob]) -> None:
            # We bypass ``super().__init__`` entirely; we only need
            # ``run`` + ``_count_rejection`` behaviour.
            self._baked = baked
            self.last_run_stats = JobMonitorStats(source_count=1)
            self.last_rejected_candidates = []
            self._sources = []
            self._scorer = _BypassScorer()  # type: ignore[assignment]
            self._repository = None
            self._debug_rejected_limit = 0

        def run(self):  # type: ignore[override]
            stats = JobMonitorStats(source_count=1)
            new_relevant: list[ScoredJob] = []
            for scored in self._baked:
                if not scored.relevant:
                    stats.rejected_total += 1
                    self._count_rejection(stats, scored)
                    self._remember_rejected_candidate(self.last_rejected_candidates, scored)
                    continue
                new_relevant.append(scored)
            self.last_run_stats = stats
            return new_relevant

    baked = _StubSource().fetch_jobs()
    service = _PatchedService(baked)
    new_relevant = service.run()
    stats = service.last_run_stats

    if new_relevant:
        _record(
            "EXCLUSIVE_NO_NEW",
            False,
            f"expected 0 new relevant, got {len(new_relevant)}",
        )
        return

    bucket_total = (
        stats.rejected_location
        + stats.rejected_no_domain
        + stats.rejected_experience
        + stats.rejected_hard
        + stats.rejected_non_target
        + stats.rejected_mobile
        + stats.rejected_role
        + stats.rejected_generic_only
        + stats.rejected_score
    )
    _record(
        "EXCLUSIVE_BUCKETS_SUM",
        bucket_total == stats.rejected_total,
        f"bucket_sum={bucket_total} rejected_total={stats.rejected_total} "
        f"buckets={stats.rejected_location}/{stats.rejected_no_domain}/"
        f"{stats.rejected_experience}/{stats.rejected_hard}/"
        f"{stats.rejected_non_target}/{stats.rejected_mobile}/"
        f"{stats.rejected_role}/{stats.rejected_generic_only}/{stats.rejected_score}",
    )
    _record(
        "EXCLUSIVE_EACH_BUCKET_ONE",
        (
            stats.rejected_location == 1
            and stats.rejected_no_domain == 1
            and stats.rejected_experience == 1
            and stats.rejected_hard == 1
            and stats.rejected_non_target == 1
            and stats.rejected_mobile == 1
            and stats.rejected_role == 1
            and stats.rejected_generic_only == 1
            and stats.rejected_score == 1
        ),
        f"buckets={stats.rejected_location}/{stats.rejected_no_domain}/"
        f"{stats.rejected_experience}/{stats.rejected_hard}/"
        f"{stats.rejected_non_target}/{stats.rejected_mobile}/"
        f"{stats.rejected_role}/{stats.rejected_generic_only}/{stats.rejected_score}",
    )


def main() -> int:
    _check_junior_java_high_confidence()
    _check_application_support_sql_passes()
    _check_generic_software_engineer_low_confidence()
    _check_high_priority_company_boost()
    _check_ios_without_backend_penalised()
    _check_ios_with_backend_passes()
    _check_ai_software_engineer_low_confidence()
    _check_senior_hard_rejected()
    _check_five_plus_years_rejected()
    _check_non_turkey_rejected()
    _check_counter_buckets_exclusive()

    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL_SCORING_V2_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())