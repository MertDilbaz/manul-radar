"""Deterministic dummy source for smoke tests and local development.

Returns three hard-coded ``Job`` instances that exercise different
branches of the future filter pipeline:

* ``Junior Java/Spring backend`` — matches include keywords, should pass
* ``Senior / Lead backend`` — matches exclude keywords, should be filtered
* ``Application Support / SQL`` — matches include keywords (different stack)

No network, no randomness: this source is safe to run in unit tests and
during local bootstrap without external dependencies.
"""
from __future__ import annotations

from datetime import datetime

from app.models.job import Job
from app.sources.base_source import BaseSource


class DummySource(BaseSource):
    """A fixture source that returns three representative fake jobs."""

    name: str = "dummy"

    def fetch_jobs(self) -> list[Job]:
        """Return three hard-coded jobs spanning the filter scenarios."""
        now = datetime.utcnow().isoformat()

        return [
            Job(
                title="Junior Java Backend Developer",
                company="SpringyCorp",
                location="Istanbul, Turkey",
                work_type="Hybrid",
                seniority="Junior",
                source=self.name,
                url="https://example.com/jobs/1",
                description="Java, Spring Boot, REST, SQL — new graduate friendly.",
                published_at="2026-06-25",
                discovered_at=now,
            ),
            Job(
                title="Senior Backend Lead",
                company="MegaScale Inc.",
                location="Remote",
                work_type="Remote",
                seniority="Senior",
                source=self.name,
                url="https://example.com/jobs/2",
                description="Senior/Lead role, 5+ years, architecture ownership.",
                published_at="2026-06-24",
                discovered_at=now,
            ),
            Job(
                title="Application Support Specialist (SQL)",
                company="ERPify",
                location="Ankara, Turkey",
                work_type="On-site",
                seniority="Mid",
                source=self.name,
                url="https://example.com/jobs/3",
                description="Application support for ERP system, SQL, integration tickets.",
                published_at="2026-06-26",
                discovered_at=now,
            ),
        ]


__all__ = ["DummySource"]