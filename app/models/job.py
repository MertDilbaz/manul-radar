"""Domain models for Manul Radar.

Currently exposes a single ``Job`` dataclass that represents a discovered
job posting in its **raw** form: only identity and source-supplied
fields. Scoring-related state (score, matched / excluded keywords,
relevance flag) is intentionally **not** part of ``Job`` — it lives on
``ScoredJob`` so that a freshly-parsed ``Job`` stays a clean value
object that can travel unchanged through SQLite, JSON caches, and the
Telegram notifier without leaking scoring decisions across layers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Job:
    """A normalized job posting discovered by a source.

    All fields except the optional ones (``location``, ``work_type``,
    ``seniority``, ``description``, ``published_at``) are required.
    Optional fields are filled when the source exposes them and left as
    ``None`` otherwise. ``discovered_at`` is always set by the source
    layer at fetch time.

    ``Job`` carries no scoring state. Use ``ScoredJob`` (built by the
    ``JobScorer``) to represent a scored view of the same posting.
    """

    title: str
    company: str
    location: str | None
    work_type: str | None
    seniority: str | None
    source: str
    url: str
    description: str | None
    published_at: str | None
    discovered_at: str


__all__ = ["Job"]
