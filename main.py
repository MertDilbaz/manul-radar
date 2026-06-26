"""Manul Sentinel — entry point.

Bootstraps the logger, loads configuration, wires the source layer into
the filter pipeline through :class:`JobMonitorService`, and reports
which jobs came back as relevant. The V1 monitoring workflow ends
there: persistence and notification are intentionally deferred to
later stages, but the full Source → Score → Filter path is now live
and exercised end-to-end on every run.
"""
from __future__ import annotations

from app.config.config_loader import load_config
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
    """
    scoring_cfg = config.get("scoring") or {}
    keywords_cfg = config.get("keywords") or {}

    include = list(keywords_cfg.get("include") or [])
    exclude = list(keywords_cfg.get("exclude") or [])
    minimum_score = int(scoring_cfg.get("minimum_score", 0))
    include_weight = int(scoring_cfg.get("include_weight", 20))
    exclude_weight = int(scoring_cfg.get("exclude_weight", 40))

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

    service = JobMonitorService(sources=sources, scorer=scorer)
    relevant_jobs = service.run()

    logger.info(f"Workflow produced {len(relevant_jobs)} relevant job(s).")
    for scored in relevant_jobs:
        logger.info(
            f"  - [{scored.job.source}] {scored.job.title} @ "
            f"{scored.job.company} | score={scored.score} | "
            f"matched={scored.matched_keywords}"
        )

    logger.info("Monitoring workflow completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
