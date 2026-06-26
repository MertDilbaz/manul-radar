"""ScoredJob — the scoring-aware view of a discovered ``Job``.

A ``ScoredJob`` wraps an immutable reference to a raw ``Job`` together
with the decision the ``JobScorer`` made about it: the numerical
``score``, which keywords matched (and which ones triggered the
exclude penalty), and a boolean ``relevant`` flag the monitor pipeline
can act on without recomputing anything.

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

from app.models.job import Job


@dataclass
class ScoredJob:
    """A ``Job`` plus the scoring decision produced for it.

    Attributes:
        job: The raw job posting the scorer was asked to evaluate.
        score: ``include_weight * matched_count + exclude_weight *
            excluded_count``. May be negative.
        matched_keywords: Subset of the configured include keywords that
            appeared in the combined job text, deduped per keyword.
        excluded_keywords: Subset of the configured exclude keywords
            that appeared in the combined job text, deduped per
            keyword.
        relevant: ``True`` iff ``score >= scorer.minimum_score``. This
            is the single source of truth for "is this job interesting";
            downstream code (notifier, persistence, scheduler) should
            check this flag rather than recomputing the threshold.
    """

    job: Job
    score: int
    matched_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    relevant: bool = False


__all__ = ["ScoredJob"]
