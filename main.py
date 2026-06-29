"""Manul Sentinel — entry point.

Bootstraps the logger, loads configuration, wires the source layer
into the filter pipeline through :class:`JobMonitorService`, persists
new relevant postings via :class:`JobRepository`, and reports what
came back. The V1 monitoring workflow ends there: notification,
scheduling, and real scraping are intentionally deferred to later
stages, but the full Source → Score → Filter → Persist path is now
live and exercised end-to-end on every run.

The repository is optional in the service contract but always wired
here. On a fresh checkout the SQLite database is created under
``data/jobs.db``; on subsequent runs URLs already in the database are
treated as already-seen and the workflow reports zero new postings.
"""
from __future__ import annotations

from app.config.config_loader import load_config
from app.database.job_repository import JobRepository
from app.filters.job_scorer import JobScorer
from app.services.job_monitor_service import JobMonitorService
from app.sources.dummy_source import DummySource
from app.utils.logger import logger, setup_logging


def _build_scorer(config: dict) -> JobScorer:
    """Construct a ``JobScorer`` from the loaded config dict.

    Reads ``scoring.minimum_score``, ``scoring.include_weight``,
    ``scoring.exclude_weight``, ``keywords.include`` and
    ``keywords.exclude``. Missing sections fall back to safe defaults
    so a partially-populated config does not crash the smoke run; a
    warning log makes the fallback visible.

    V2 (2026-06-29): also reads the new tiered weights and keyword
    buckets (``strong_weight``, ``weak_weight``, ``location_weight``,
    ``company_boost_weight``, ``mobile_penalty``,
    ``generic_only_penalty``, ``weak_keywords``,
    ``company_boost_keywords``, ``mobile_negative_keywords``,
    ``high_confidence_min_score``, ``low_confidence_min_score``) so
    generic "Software Engineer" postings no longer score alongside
    strong junior+java hits.
    """
    scoring_cfg = config.get("scoring") or {}
    keywords_cfg = config.get("keywords") or {}

    include = list(keywords_cfg.get("include") or [])
    exclude = list(keywords_cfg.get("exclude") or [])
    hard_exclude = list(keywords_cfg.get("hard_exclude") or [])
    domain_required = list(keywords_cfg.get("domain_required") or [])
    non_target_domain = list(keywords_cfg.get("non_target_domain") or [])
    source_boost = list(keywords_cfg.get("source_boost") or [])
    location_required = list(keywords_cfg.get("location_required") or [])
    location_reject = list(keywords_cfg.get("location_reject") or [])
    role_required = list(keywords_cfg.get("role_required") or [])
    weak_keywords = list(keywords_cfg.get("weak_keywords") or [])
    company_boost_keywords = list(keywords_cfg.get("company_boost_keywords") or [])
    mobile_negative_keywords = list(keywords_cfg.get("mobile_negative_keywords") or [])
    minimum_score = int(scoring_cfg.get("minimum_score", 0))
    include_weight = int(scoring_cfg.get("include_weight", 20))
    exclude_weight = int(scoring_cfg.get("exclude_weight", 40))
    source_boost_weight = int(scoring_cfg.get("source_boost_weight", 8))
    hard_exclude_experience_years_raw = scoring_cfg.get(
        "hard_exclude_experience_years",
        4,
    )
    hard_exclude_experience_years = (
        int(hard_exclude_experience_years_raw)
        if hard_exclude_experience_years_raw is not None
        else None
    )
    strong_weight = scoring_cfg.get("strong_weight")
    weak_weight = int(scoring_cfg.get("weak_weight", 8))
    location_weight = int(scoring_cfg.get("location_weight", 10))
    company_boost_weight = int(scoring_cfg.get("company_boost_weight", 10))
    mobile_penalty = int(scoring_cfg.get("mobile_penalty", 25))
    generic_only_penalty = int(scoring_cfg.get("generic_only_penalty", 25))
    high_confidence_min_score = int(scoring_cfg.get("high_confidence_min_score", 80))
    high_confidence_min_strong = int(scoring_cfg.get("high_confidence_min_strong", 1))
    low_confidence_min_score = int(scoring_cfg.get("low_confidence_min_score", 40))

    if not include and not exclude:
        logger.warning(
            "No keywords configured — every job will score 0 and be "
            "marked irrelevant unless minimum_score is also <= 0."
        )

    return JobScorer(
        include_keywords=include,
        exclude_keywords=exclude,
        minimum_score=minimum_score,
        include_weight=include_weight,
        exclude_weight=exclude_weight,
        hard_exclude_keywords=hard_exclude,
        hard_exclude_experience_years=hard_exclude_experience_years,
        domain_required_keywords=domain_required,
        non_target_domain_keywords=non_target_domain,
        source_boost_keywords=source_boost,
        source_boost_weight=source_boost_weight,
        location_required_keywords=location_required,
        location_reject_keywords=location_reject,
        role_required_keywords=role_required,
        weak_keywords=weak_keywords,
        company_boost_keywords=company_boost_keywords,
        mobile_negative_keywords=mobile_negative_keywords,
        strong_weight=strong_weight,
        weak_weight=weak_weight,
        location_weight=location_weight,
        company_boost_weight=company_boost_weight,
        mobile_penalty=mobile_penalty,
        generic_only_penalty=generic_only_penalty,
        high_confidence_min_score=high_confidence_min_score,
        high_confidence_min_strong=high_confidence_min_strong,
        low_confidence_min_score=low_confidence_min_score,
    )


def main() -> int:
    """Run the Manul Sentinel bootstrap sequence."""
    setup_logging()

    logger.info("Manul Sentinel starting...")

    try:
        config = load_config()
    except FileNotFoundError as exc:
        logger.error(f"Configuration error: {exc}")
        return 1

    minimum_score = (config.get("scoring") or {}).get("minimum_score")
    logger.info(
        f"Config loaded successfully. minimum_score={minimum_score}"
    )

    scorer = _build_scorer(config)
    sources = [DummySource()]

    repository = JobRepository(db_path="data/jobs.db")
    try:
        repository.init_db()
    except Exception as exc:
        logger.error(f"Failed to initialize repository: {exc}")
        return 1
    logger.info(f"Repository ready at {repository.db_path}.")

    service = JobMonitorService(
        sources=sources,
        scorer=scorer,
        repository=repository,
    )
    new_relevant = service.run()

    logger.info(
        f"Workflow produced {len(new_relevant)} new relevant job(s)."
    )
    for scored in new_relevant:
        logger.info(
            f"  - [{scored.job.source}] {scored.job.title} @ "
            f"{scored.job.company} | score={scored.score} | "
            f"matched={scored.matched_keywords}"
        )

    logger.info("Monitoring workflow completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
