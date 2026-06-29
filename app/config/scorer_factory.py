"""Factory for building a ``JobScorer`` from the loaded config dict.

This module is the single source of truth for translating ``config.yaml``
(``scoring`` + ``keywords`` sections) into a fully-configured
:class:`~app.filters.job_scorer.JobScorer`.

Before this module existed, the same ~90-line translation logic was
duplicated between ``main.py`` and ``run_monitor.py``. The two copies
had already started to diverge (one had a keyword-emptiness warning,
the other did not), so every new config key risked being wired in one
entry point but not the other. Centralising the factory here means a
future scoring parameter only needs to be added once.
"""
from __future__ import annotations

from app.filters.job_scorer import JobScorer
from app.utils.logger import logger


def build_scorer_from_config(config: dict) -> JobScorer:
    """Construct a ``JobScorer`` from the loaded config dict.

    Reads ``scoring.minimum_score``, ``scoring.include_weight``,
    ``scoring.exclude_weight``, ``keywords.include`` and
    ``keywords.exclude``. Missing sections fall back to safe defaults
    so a partially-populated config does not crash the run; a warning
    log makes the fallback visible.

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
    # V2 keyword buckets
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

    # V2 tiered weights
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
        # V2 keyword buckets
        weak_keywords=weak_keywords,
        company_boost_keywords=company_boost_keywords,
        mobile_negative_keywords=mobile_negative_keywords,
        # V2 tiered weights
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


__all__ = ["build_scorer_from_config"]
