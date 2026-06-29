"""Remotive public remote-jobs API source.

Remotive exposes a free, no-auth JSON endpoint for searching remote
job postings:::

    https://remotive.com/api/remote-jobs?search=junior&limit=50
    https://remotive.com/api/remote-jobs?category=software-dev&search=junior

The response is a single JSON object of the form::

    {"0-legal-notice": ..., "job-count": N, "jobs": [ { ... }, ... ]}

where each job dict contains ``id``, ``title``, ``company_name``,
``candidate_required_location``, ``url``, ``description``,
``publication_date``, ``job_type`` and ``salary``.

This source is tuned for entry-level discovery: the default ``search``
term is ``"junior"`` and the default ``category`` is ``"software-dev"``,
so out of the box it surfaces junior software development postings.
Both are configurable via the constructor for callers that want to
broaden or narrow the query.
"""
from __future__ import annotations

from app.models.job import Job
from app.sources.ats_helpers import (
    JSON_HEADERS,
    REQUEST_TIMEOUT,
    fetch_with_retry,
    html_to_text,
    make_job,
    utc_now_iso,
)
from app.sources.base_source import BaseSource
from app.utils.logger import logger


class RemotiveSource(BaseSource):
    """Fetch remote jobs from Remotive's public JSON API.

    Args:
        search: Free-text search term. Defaults to ``"junior"`` to bias
            the feed toward entry-level postings.
        category: Remotive category slug (e.g. ``"software-dev"``,
            ``"data"``). Defaults to ``"software-dev"``.
        source_name: Name stamped on every emitted ``Job.source``.
            Defaults to ``"remotive"``.
        limit: Maximum number of jobs to request from the API.
        timeout: Per-request timeout in seconds.
    """

    name: str = ""
    search: str = "junior"
    category: str = "software-dev"
    limit: int = 50
    timeout: int = REQUEST_TIMEOUT
    api_url: str = ""

    def __init__(
        self,
        search: str = "junior",
        category: str = "software-dev",
        source_name: str = "remotive",
        limit: int = 50,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        self.search = (search or "junior").strip() or "junior"
        self.category = (category or "software-dev").strip() or "software-dev"
        self.limit = max(1, int(limit))
        self.timeout = int(timeout)
        self.name = source_name or "remotive"
        self.api_url = self._build_url()

    def _build_url(self) -> str:
        """Compose the Remotive API URL from search/category/limit."""
        params = [f"category={self.category}", f"search={self.search}", f"limit={self.limit}"]
        return "https://remotive.com/api/remote-jobs?" + "&".join(params)

    def fetch_jobs(self) -> list[Job]:
        """GET the Remotive feed and return normalized ``Job`` instances.

        On a network or HTTP error the source logs the failure and
        returns ``[]`` rather than propagating the exception, so one
        flaky source never takes down the whole monitor run.
        """
        try:
            response = fetch_with_retry(
                self.api_url,
                headers=JSON_HEADERS,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 — surface as zero results
            logger.warning(
                "Remotive source '{}' could not fetch {}: {}",
                self.name,
                self.api_url,
                exc,
            )
            return []

        if not isinstance(data, dict):
            logger.warning("Remotive source '{}' got non-dict response; returning [].", self.name)
            return []

        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list):
            return []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            company = item.get("company_name") or ""
            location = item.get("candidate_required_location") or ""
            description = html_to_text(item.get("description"))
            work_type = item.get("job_type") or None
            published_at = item.get("publication_date") or None
            job = make_job(
                title=title,
                company=company,
                location=location or None,
                source=self.name,
                url=url,
                description=description,
                work_type=work_type,
                published_at=published_at,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)

        logger.info("Remotive source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs


__all__ = ["RemotiveSource"]
