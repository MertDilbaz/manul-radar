"""ScoredJob — the scoring-aware view of a discovered ``Job``.

A ``ScoredJob`` wraps an immutable reference to a raw ``Job`` together
with the decision the ``JobScorer`` made about it: the numerical
``score``, which keywords matched (and which ones triggered the
exclude penalty), a boolean ``relevant`` flag, and the *human-readable*
``confidence`` tier + reasons the notifier surfaces in Telegram.

The split keeps the source / persistence layers from accidentally
mutating scoring state (or vice-versa) and lets the monitor service
treat ``ScoredJob`` as a flat record it can filter, forward, or store
without re-walking the original ``Job``.

Note: ``ScoredJob`` is intentionally *not* a frozen dataclass. The
scorer constructs it fresh on every call, so there is no shared
mutable state to defend against, and frozen would block legitimate
post-processing in future filters (e.g. tagging, dedup hints).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models.job import Job

Confidence = Literal["high", "medium", "low"]


@dataclass
class ScoredJob:
    """A ``Job`` plus the scoring decision produced for it.

    Attributes:
        job: The raw job posting the scorer was asked to evaluate.
        score: Weighted sum of matched / excluded / penalty signals.
            May be negative. See :class:`app.filters.job_scorer.JobScorer`
            for the exact formula.
        matched_keywords: Subset of the configured include keywords that
            appeared in the combined job text, deduped per keyword.
        excluded_keywords: Subset of the configured exclude keywords
            that appeared in the combined job text, deduped per
            keyword.
        relevant: ``True`` iff the job survived every hard gate
            (location, domain, role, experience, hard-exclude,
            non-target) AND ``score >= scorer.minimum_score``. This is
            the single source of truth for "is this job interesting";
            downstream code (notifier, persistence, scheduler) should
            check this flag rather than recomputing the threshold.
        confidence: Human-readable tier (``"high"`` / ``"medium"`` /
            ``"low"``) for relevant jobs. ``""`` (empty string) means
            the scorer did not assign a confidence (e.g. for rejected
            jobs). Telegram notifier surfaces this so Mert can tell at
            a glance whether a "Software Engineer" hit is a strong
            junior+java match or a low-confidence generic listing.
        confidence_reasons: Short, human-readable reasons backing the
            confidence call (e.g. ``"junior + java + sql"``,
            ``"generic software engineer only"``, ``"ios without
            backend signals"``). Empty for rejected jobs.
    """

    job: Job
    score: int
    matched_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    relevant: bool = False
    confidence: str = ""
    confidence_reasons: list[str] = field(default_factory=list)


__all__ = ["ScoredJob", "Confidence"]
