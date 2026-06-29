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
    """Operational counters from the most recent monitor run.

    V2 semantics (2026-06-29): rejection counters are now **exclusive**
    — every rejected job is counted in exactly one bucket based on its
    primary reject reason (location > domain > experience > hard >
    non-target > mobile > role > generic-only > score). The total of
    the per-bucket counters therefore equals ``rejected_total``
    instead of exceeding it, which makes the Telegram summary easier
    to read.
    """

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
    rejected_mobile: int = 0
    rejected_generic_only: int = 0
    rejected_score: int = 0
    source_errors: int = 0
    source_error_names: list[str] = field(default_factory=list)


class JobMonitorService:
    """Run all sources through the scorer and optional repository."""

    # Priority order used by ``_primary_reject_reason``: a rejected job
    # is bucketed into the highest-priority bucket that applies. This
    # is what makes the per-bucket counters sum to ``rejected_total``.
    _REJECT_PRIORITY: tuple[str, ...] = (
        "location",
        "domain",
        "experience",
        "hard",
        "non_target",
        "mobile",
        "role",
        "generic_only",
        "score",
    )

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

    def _primary_reject_reason(self, scored: ScoredJob) -> str:
        """Return the highest-priority reject bucket for ``scored``.

        The order matches ``_REJECT_PRIORITY`` and the prefixes used by
        :class:`app.filters.job_scorer.JobScorer`.
        """
        excluded = list(scored.excluded_keywords or [])
        hard_keywords = set(getattr(self._scorer, "hard_exclude_keywords", tuple()))

        if self._has_prefix(excluded, "location:"):
            return "location"
        if self._has_prefix(excluded, "domain:"):
            return "domain"
        if self._has_prefix(excluded, "experience:"):
            return "experience"
        if any(value in hard_keywords for value in excluded):
            return "hard"
        if self._has_prefix(excluded, "non_target:"):
            return "non_target"
        if self._has_prefix(excluded, "mobile:"):
            return "mobile"
        if self._has_prefix(excluded, "role:"):
            return "role"
        if self._has_prefix(excluded, "generic_only:"):
            return "generic_only"
        return "score"

    def _count_rejection(self, stats: JobMonitorStats, scored: ScoredJob) -> None:
        """Increment exactly one reject bucket per rejected job.

        This is the V2 contract: each rejected job is counted once,
        under its highest-priority reject reason. The per-bucket sum
        always equals ``rejected_total`` so the Telegram summary no
        longer adds up to a number larger than the total.
        """
        reason = self._primary_reject_reason(scored)
        if reason == "location":
            stats.rejected_location += 1
        elif reason == "domain":
            stats.rejected_no_domain += 1
        elif reason == "experience":
            stats.rejected_experience += 1
        elif reason == "hard":
            stats.rejected_hard += 1
        elif reason == "non_target":
            stats.rejected_non_target += 1
        elif reason == "mobile":
            stats.rejected_mobile += 1
        elif reason == "role":
            stats.rejected_role += 1
        elif reason == "generic_only":
            stats.rejected_generic_only += 1
        else:
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
    def _primary_reject_reason_legacy(scored: ScoredJob) -> str:
        """Return a short human-readable reject reason for log output."""
        excluded = list(scored.excluded_keywords or [])
        if any(value.startswith("location:") for value in excluded):
            return "location"
        if any(value.startswith("domain:") for value in excluded):
            return "no_domain"
        if any(value.startswith("role:") for value in excluded):
            return "role"
        if any(value.startswith("experience:") for value in excluded):
            return "experience"
        if any(value.startswith("non_target:") for value in excluded):
            return "non_target"
        if any(value.startswith("mobile:") for value in excluded):
            return "mobile_no_backend"
        if any(value.startswith("generic_only:") for value in excluded):
            return "generic_only"
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
            confidence = scored.confidence or "-"
            logger.info(
                f"  REJECTED[{index}] score={scored.score} "
                f"reason={self._primary_reject_reason_legacy(scored)} "
                f"confidence={confidence} | "
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
        # Per-bucket counters are now exclusive and sum to rejected_total,
        # so the summary reads naturally: "X rejected (Y because …, Z
        # because …)" instead of "X rejected (X+Y+Z+… > X)".
        logger.info(
            f"JobMonitorService completed: {stats.new_relevant} new relevant "
            f"out of {stats.total_seen} total{repo_summary}; "
            f"rejected={stats.rejected_total} "
            f"(location={stats.rejected_location}, "
            f"no_domain={stats.rejected_no_domain}, "
            f"experience={stats.rejected_experience}, "
            f"hard={stats.rejected_hard}, "
            f"non_target={stats.rejected_non_target}, "
            f"mobile={stats.rejected_mobile}, "
            f"role={stats.rejected_role}, "
            f"generic_only={stats.rejected_generic_only}, "
            f"score={stats.rejected_score})"
            f"{source_error_summary}."
        )
        self._log_top_rejected()
        return new_relevant


__all__ = ["JobMonitorService", "JobMonitorStats"]
