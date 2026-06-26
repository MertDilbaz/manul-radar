"""Source contract for Manul Radar.

A *source* is anything that can produce a list of normalized ``Job``
instances: a scraper for a public job board, an API client for an ATS,
a RSS/Atom reader, or a deterministic dummy for tests. Every source
implements the same ``fetch_jobs`` contract so the rest of the pipeline
(filtering, scoring, persistence, notification) can stay source-agnostic.

Sources are deliberately narrow: they only *fetch*. Filtering, scoring,
deduplication, persistence, and notifications all live in their own
modules. This keeps each layer independently testable and lets us add
a new source without touching anything else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.job import Job


class BaseSource(ABC):
    """Abstract base class for all job sources.

    Concrete subclasses must set the ``name`` class attribute and
    implement :meth:`fetch_jobs`. The name is used downstream for
    logging, deduplication keys, and source-attribution in notifications.
    """

    name: str = ""

    @abstractmethod
    def fetch_jobs(self) -> list[Job]:
        """Return all jobs discovered by this source.

        Implementations should return a (possibly empty) list of
        fully-populated ``Job`` instances. ``discovered_at`` must be
        set by the source at fetch time. Filtering, scoring, and
        persistence are the caller's responsibility.
        """
        raise NotImplementedError


__all__ = ["BaseSource"]