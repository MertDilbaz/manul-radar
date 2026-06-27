"""Job monitor service — source fetch + scoring + repository orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.database.job_repository import JobRepository
from app.filters.job_scorer import JobScorer
from app.models.scored_job import ScoredJob
from app.sources.base_source import BaseSource
from app.utils.logger import logger


@dataclass
class JobMonitorStats:
    """Operational counters from the most recent monitor run."""

    source_count: int = 0
    total_seen: int = 0
    relevant_total: int = 0
    new_relevant: int = 0
    already_seen: int = 0
    rejected_total: int = 0
    rejected_no_domain: int = 0
    rejected_location: int = 0
    rejected_role: int = 0
    rejected_non_target: int = 0
    rejected_hard: int = 0
    rejected_experience: int = 0
    rejected_soft: int = 0
    rejected_score: int = 0
    source_errors: int = 0
    source_error_names: list[str] = field(default_factory=list)


class JobMonitorService:
    """Run all sources through the scorer and optional repository."""

    def __init__(
        self,
        sources: list[BaseSource],
        scorer: JobScorer,
        repository: JobRepository | None = None,
        debug_rejected_limit: int = 20,
    ) -> None:
        self._sources: list[BaseSource] = list(sources)
        self._scorer: JobScorer = scorer
        self._repository: JobRepository | None = repository
        self._debug_rejected_limit: int = max(0, int(debug_rejected_limit))
        self.last_run_stats = JobMonitorStats(source_count=len(self._sources))
        self.last_rejected_candidates: list[ScoredJob] = []

    @property
    def has_repository(self) -> bool:
        return self._repository is not None

    @staticmethod
    def _has_prefix(values: list[str], prefix: str) -> bool:
        return any(value.startswith(prefix) for value in values)

    @staticmethod
    def _has_any(values: list[str], needles: tuple[str, ...]) -> bool:
        needle_set = set(needles)
        return any(value in needle_set for value in values)

    def _count_rejection(self, stats: JobMonitorStats, scored: ScoredJob) -> None:
        excluded = list(scored.excluded_keywords or [])
        hard_keywords = getattr(self._scorer, "hard_exclude_keywords", tuple())
        soft_keywords = getattr(self._scorer, "soft_exclude_keywords", tuple())

        has_domain = self._has_prefix(excluded, "domain:no_technology_signal")
        has_location = self._has_prefix(excluded, "location:")
        has_role = self._has_prefix(excluded, "role:")
        has_non_target = self._has_prefix(excluded, "non_target:")
        has_experience = self._has_prefix(excluded, "experience:")
        has_hard = self._has_any(excluded, hard_keywords)
        has_soft = self._has_any(excluded, soft_keywords)

        if has_domain:
            stats.rejected_no_domain += 1
        if has_location:
            stats.rejected_location += 1
        if has_role:
            stats.rejected_role += 1
        if has_non_target:
            stats.rejected_non_target += 1
        if has_experience:
            stats.rejected_experience += 1
        if has_hard:
            stats.rejected_hard += 1
        if has_soft:
            stats.rejected_soft += 1

        # Score rejection means the posting passed hard/domain/experience gates
        # but still did not reach the minimum score. This is the most important
        # bucket for calibration.
        if not (has_domain or has_location or has_role or has_experience or has_hard or has_non_target):
            stats.rejected_score += 1

    def _remember_rejected_candidate(
        self,
        rejected_candidates: list[ScoredJob],
        scored: ScoredJob,
    ) -> None:
        if self._debug_rejected_limit <= 0:
            return
        # Keep candidates that had at least one positive signal. Purely random
        # no-domain postings are not useful for calibration.
        if scored.score <= 0 and not scored.matched_keywords:
            return
        rejected_candidates.append(scored)

    @staticmethod
    def _primary_reject_reason(scored: ScoredJob) -> str:
        excluded = list(scored.excluded_keywords or [])
        if any(value.startswith("location:") for value in excluded):
            return "location"
        if any(value.startswith("domain:") for value in excluded):
            return "no_domain"
        if any(value.startswith("role:") for value in excluded):
            return "role"
        if any(value.startswith("experience:") for value in excluded):
            return "experience"
        if excluded:
            return ", ".join(excluded[:3])
        return "score_below_threshold"

    def _log_top_rejected(self) -> None:
        if not self.last_rejected_candidates:
            return
        logger.info(
            "Top rejected candidates for scoring calibration "
            f"(showing {len(self.last_rejected_candidates)}):"
        )
        for index, scored in enumerate(self.last_rejected_candidates, start=1):
            matched = ", ".join((scored.matched_keywords or [])[:8]) or "-"
            excluded = ", ".join((scored.excluded_keywords or [])[:8]) or "-"
            logger.info(
                f"  REJECTED[{index}] score={scored.score} "
                f"reason={self._primary_reject_reason(scored)} | "
                f"[{scored.job.source}] {scored.job.title} @ {scored.job.company} | "
                f"matched={matched} | excluded={excluded} | url={scored.job.url}"
            )

    def run(self) -> list[ScoredJob]:
        """Fetch, score, dedup, save-new and return new relevant jobs."""
        source_names = [s.name for s in self._sources]
        logger.info(
            f"JobMonitorService starting with {len(self._sources)} "
            f"source(s): {source_names}."
        )

        stats = JobMonitorStats(source_count=len(self._sources))
        new_relevant: list[ScoredJob] = []
        rejected_candidates: list[ScoredJob] = []

        for source in self._sources:
            try:
                jobs = source.fetch_jobs()
            except Exception as exc:  # noqa: BLE001 — source failure isolation
                stats.source_errors += 1
                stats.source_error_names.append(source.name)
                logger.error(
                    f"Source '{source.name}' failed: {exc}. "
                    "Skipping and continuing with next source."
                )
                continue

            logger.info(
                f"Source '{source.name}' returned {len(jobs)} job(s); scoring."
            )
            stats.total_seen += len(jobs)

            for job in jobs:
                scored = self._scorer.score(job)
                if not scored.relevant:
                    stats.rejected_total += 1
                    self._count_rejection(stats, scored)
                    self._remember_rejected_candidate(rejected_candidates, scored)
                    continue

                stats.relevant_total += 1

                if self._repository is not None:
                    if self._repository.has_seen(scored.job):
                        stats.already_seen += 1
                        logger.debug(
                            f"Skipping already-seen job: {scored.job.url}"
                        )
                        continue
                    self._repository.save(scored)

                new_relevant.append(scored)

        new_relevant.sort(key=lambda item: item.score, reverse=True)
        stats.new_relevant = len(new_relevant)
        self.last_run_stats = stats
        self.last_rejected_candidates = sorted(
            rejected_candidates,
            key=lambda item: item.score,
            reverse=True,
        )[: self._debug_rejected_limit]

        repo_summary = (
            f", {stats.already_seen} already in repository"
            if self._repository is not None
            else ""
        )
        source_error_summary = (
            f", source_errors={stats.source_errors} ({', '.join(stats.source_error_names[:5])})"
            if stats.source_errors
            else ""
        )
        logger.info(
            f"JobMonitorService completed: {stats.new_relevant} new relevant "
            f"out of {stats.total_seen} total{repo_summary}; "
            f"rejected={stats.rejected_total} "
            f"(location={stats.rejected_location}, "
            f"no_domain={stats.rejected_no_domain}, "
            f"role={stats.rejected_role}, "
            f"non_target={stats.rejected_non_target}, "
            f"hard={stats.rejected_hard}, "
            f"experience={stats.rejected_experience}, "
            f"soft={stats.rejected_soft}, "
            f"score={stats.rejected_score})"
            f"{source_error_summary}."
        )
        self._log_top_rejected()
        return new_relevant


__all__ = ["JobMonitorService", "JobMonitorStats"]
