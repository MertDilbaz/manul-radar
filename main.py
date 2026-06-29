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
from app.config.scorer_factory import build_scorer_from_config
from app.database.job_repository import JobRepository
from app.services.job_monitor_service import JobMonitorService
from app.sources.dummy_source import DummySource
from app.utils.logger import logger, setup_logging


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

    scorer = build_scorer_from_config(config)
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
