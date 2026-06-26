"""Job monitor service — the V1 workflow orchestrator.

``JobMonitorService`` is the single point where the source layer and
the filter layer meet. Its job is small and explicit:

* run every configured source in order,
* score the jobs the source produced,
* keep only the jobs the scorer marked ``relevant``,
* return that filtered list so the caller (currently ``main.py``) can
  log, persist, or notify on it.

The service is deliberately **not** the place where persistence,
notification, scheduling, or retry policy live. Those are downstream
consumers of the returned ``ScoredJob`` list. This keeps the
service testable in isolation (no DB, no network, no clock) and lets
us evolve each downstream concern independently.

Failure isolation: if a single source raises, the exception is logged
and the loop continues with the next source. A flaky scraper must not
take down the whole monitoring run. Per-job scoring exceptions are
**not** swallowed in V1 — they would indicate a real bug (malformed
``Job`` or scorer regression), so they propagate up. We can revisit
this when a real source goes into production.
"""
from __future__ import annotations

from app.filters.job_scorer import JobScorer
from app.models.scored_job import ScoredJob
from app.sources.base_source import BaseSource
from app.utils.logger import logger


class JobMonitorService:
    """Run all sources through the scorer and return relevant ``ScoredJob``s."""

    def __init__(
        self,
        sources: list[BaseSource],
        scorer: JobScorer,
    ) -> None:
        """Store the configured sources and scorer.

        ``sources`` is iterated as a defensive copy so a caller mutating
        the original list after construction does not affect us.
        ``scorer`` is assumed immutable for the service's lifetime —
        ``JobScorer`` has no mutator API today, so this is safe.
        """
        self._sources: list[BaseSource] = list(sources)
        self._scorer: JobScorer = scorer

    def run(self) -> list[ScoredJob]:
        """Fetch from every source, score, filter, and return the relevant ones."""
        source_names = [s.name for s in self._sources]
        logger.info(
            f"JobMonitorService starting with {len(self._sources)} "
            f"source(s): {source_names}."
        )

        relevant_jobs: list[ScoredJob] = []
        total_seen = 0

        for source in self._sources:
            try:
                jobs = source.fetch_jobs()
            except Exception as exc:  # noqa: BLE001 — we want any failure isolated
                logger.error(
                    f"Source '{source.name}' failed: {exc}. "
                    "Skipping and continuing with next source."
                )
                continue

            logger.info(
                f"Source '{source.name}' returned {len(jobs)} job(s); scoring."
            )
            total_seen += len(jobs)

            for job in jobs:
                scored = self._scorer.score(job)
                if scored.relevant:
                    relevant_jobs.append(scored)

        logger.info(
            f"JobMonitorService completed: {len(relevant_jobs)} relevant "
            f"out of {total_seen} total."
        )
        return relevant_jobs


__all__ = ["JobMonitorService"]
