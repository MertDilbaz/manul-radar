"""Job monitor service — the V1 workflow orchestrator.

``JobMonitorService`` is the single point where the source layer, the
filter layer, and (optionally) the persistence layer meet. Its job:

* run every configured source in order,
* score the jobs the source produced,
* keep only the jobs the scorer marked ``relevant``,
* if a ``JobRepository`` is wired in, also dedup against it: skip
  postings that were already persisted, save the rest, and only
  return the **new** relevant jobs to the caller,
* return that filtered list so the caller (``main.py`` today) can log
  or notify on it.

The service is deliberately **not** the place where notification,
scheduling, or retry policy live. Those are downstream consumers of
the returned ``ScoredJob`` list. Keeping persistence optional means
unit tests can run the scoring / filtering pipeline without touching
SQLite, while ``main.py`` opts into the dedup + save path with a
single constructor argument.

Failure isolation: if a single source raises, the exception is logged
and the loop continues with the next source. A flaky scraper must not
take down the whole monitoring run. Per-job scoring exceptions are
**not** swallowed in V1 — they would indicate a real bug (malformed
``Job`` or scorer regression), so they propagate up. Repository I/O
errors are **not** swallowed either for the same reason: silent data
loss in the persistence layer is worse than a hard crash.
"""
from __future__ import annotations

from app.database.job_repository import JobRepository
from app.filters.job_scorer import JobScorer
from app.models.scored_job import ScoredJob
from app.sources.base_source import BaseSource
from app.utils.logger import logger


class JobMonitorService:
    """Run all sources through the scorer and (optionally) the repository."""

    def __init__(
        self,
        sources: list[BaseSource],
        scorer: JobScorer,
        repository: JobRepository | None = None,
    ) -> None:
        """Store the configured sources, scorer, and optional repository.

        ``sources`` is iterated as a defensive copy so a caller mutating
        the original list after construction does not affect us.
        ``scorer`` and ``repository`` are assumed immutable for the
        service's lifetime — neither exposes a mutator API today.

        ``repository=None`` keeps the historical "return every relevant
        job" behavior, which is what the scorer-only unit tests expect.
        Wiring a repository switches the service into "dedup + save"
        mode and changes the meaning of the returned list: only
        *previously unseen* relevant jobs come back.
        """
        self._sources: list[BaseSource] = list(sources)
        self._scorer: JobScorer = scorer
        self._repository: JobRepository | None = repository

    @property
    def has_repository(self) -> bool:
        """Whether this service is wired to persist via a repository."""
        return self._repository is not None

    def run(self) -> list[ScoredJob]:
        """Fetch, score, dedup-against-repo, save-new, return new relevant jobs.

        When a repository is wired in, the returned list is the subset
        of relevant ``ScoredJob``s that were **not** already persisted.
        Already-seen relevant jobs are filtered out before being added
        to the return value but are still counted in the run summary
        log so operators can see "0 new, 2 already in repo" at a glance.
        """
        source_names = [s.name for s in self._sources]
        logger.info(
            f"JobMonitorService starting with {len(self._sources)} "
            f"source(s): {source_names}."
        )

        new_relevant: list[ScoredJob] = []
        total_seen = 0
        skipped_already_seen = 0

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
                if not scored.relevant:
                    continue

                if self._repository is not None:
                    if self._repository.has_seen(scored.job):
                        skipped_already_seen += 1
                        logger.debug(
                            f"Skipping already-seen job: {scored.job.url}"
                        )
                        continue
                    self._repository.save(scored)

                new_relevant.append(scored)

        repo_summary = (
            f", {skipped_already_seen} already in repository"
            if self._repository is not None
            else ""
        )
        logger.info(
            f"JobMonitorService completed: {len(new_relevant)} new relevant "
            f"out of {total_seen} total{repo_summary}."
        )
        return new_relevant


__all__ = ["JobMonitorService"]
