"""Keyword-based job scorer for Manul Radar.

The scorer is the V1 filter pipeline's only intelligence: it knows about
include / exclude keyword lists, a minimum score threshold, and the
per-hit weights, and it applies them to a ``Job`` instance. It
deliberately knows nothing about Telegram, SQLite, scraping, or
notification — those are downstream consumers that receive the scored
result.

Scoring rules (V1):

* ``+include_weight`` for every include keyword that appears in the
  combined job text. A keyword that appears multiple times still counts
  once.
* ``-exclude_weight`` for every exclude keyword that appears. Same
  dedup rule.
* ``score = include_weight * matched_count - exclude_weight * excluded_count``.
* A job is *relevant* iff ``score >= minimum_score``.

``include_weight`` defaults to ``20`` and ``exclude_weight`` to ``40``
(positive magnitudes — the scorer subtracts the exclude contribution).
Weights come from ``config.scoring`` when the scorer is wired by
``main.py`` so the scoring policy is data-driven.

The combined text is the normalized concatenation of the textual
``Job`` fields (``title``, ``company``, ``location``, ``work_type``,
``seniority``, ``description``). Identity fields (``source``, ``url``,
``published_at``, ``discovered_at``) are excluded — they would skew
matches (every URL contains ``"http"``, etc.) without adding signal.

Scoring returns a fresh :class:`ScoredJob` that wraps the original
``Job``. The input ``Job`` is never mutated: the source layer can keep
its own copy for re-scoring against a different policy later.
"""
from __future__ import annotations

from app.filters.job_text import normalize_text
from app.models.job import Job
from app.models.scored_job import ScoredJob


class JobScorer:
    """Score ``Job`` instances against include / exclude keyword lists."""

    _SCORED_FIELDS: tuple[str, ...] = (
        "title",
        "company",
        "location",
        "work_type",
        "seniority",
        "description",
    )

    def __init__(
        self,
        include_keywords: list[str],
        exclude_keywords: list[str],
        minimum_score: int,
        include_weight: int = 20,
        exclude_weight: int = 40,
    ) -> None:
        """Store keyword lists, threshold, and per-hit weights.

        ``include_weight`` is added per matched include keyword;
        ``exclude_weight`` is the *magnitude* subtracted per matched
        exclude keyword (i.e. config-side values are positive).

        Keywords are pre-normalized (lowercased + whitespace-collapsed)
        once here so per-job scoring stays cheap and predictable.
        """
        self._include: list[str] = [
            normalize_text(kw) for kw in include_keywords if kw
        ]
        self._exclude: list[str] = [
            normalize_text(kw) for kw in exclude_keywords if kw
        ]
        self._minimum_score: int = int(minimum_score)
        self._include_weight: int = int(include_weight)
        self._exclude_weight: int = int(exclude_weight)

    @property
    def minimum_score(self) -> int:
        """The configured relevance threshold."""
        return self._minimum_score

    def _combined_text(self, job: Job) -> str:
        """Return the concatenated, normalized text used for matching."""
        parts: list[str] = []
        for field_name in self._SCORED_FIELDS:
            value = getattr(job, field_name)
            parts.append(normalize_text(value))
        return " ".join(parts)

    def _match_keywords(self, text: str, keywords: list[str]) -> list[str]:
        """Return each keyword that appears as a substring of ``text``.

        Duplicates in the input keyword list are preserved as repeated
        entries — the V1 contract is one match per configured keyword,
        not per *unique* keyword, so the caller controls whether to
        pass a deduped list or not.
        """
        return [kw for kw in keywords if kw and kw in text]

    def score(self, job: Job) -> ScoredJob:
        """Score ``job`` and return a new ``ScoredJob`` wrapping it.

        ``relevant`` is set to ``score >= minimum_score`` so downstream
        code can filter on a single boolean without recomputing.
        """
        text = self._combined_text(job)
        matched = self._match_keywords(text, self._include)
        excluded = self._match_keywords(text, self._exclude)

        new_score = (
            self._include_weight * len(matched)
            - self._exclude_weight * len(excluded)
        )
        relevant = new_score >= self._minimum_score

        return ScoredJob(
            job=job,
            score=new_score,
            matched_keywords=matched,
            excluded_keywords=excluded,
            relevant=relevant,
        )


__all__ = ["JobScorer"]
