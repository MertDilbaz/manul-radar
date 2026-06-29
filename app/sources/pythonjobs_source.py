"""Python.org job board source.

Python.org hosts a public job board at https://www.python.org/jobs/.
The old RSS feed (``/jobs/feed/``) was deprecated and now returns 404.
This source scrapes the HTML listing page instead.

The job board lists Python-focused roles — many are entry-level /
junior developer positions that explicitly mention Python, making this
a useful complement to the broader API-based sources.

Each job listing is an ``<article>`` or ``<li>`` element inside the
``.job-list`` container with a link to the detail page.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models.job import Job
from app.sources.ats_helpers import (
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    clean_text,
    fetch_with_retry,
    make_job,
    utc_now_iso,
)
from app.sources.base_source import BaseSource
from app.utils.logger import logger


_JOBS_URL = "https://www.python.org/jobs/"


class PythonJobsSource(BaseSource):
    """Fetch jobs from the Python.org job board.

    Args:
        source_name: Name stamped on every emitted ``Job.source``.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        source_name: str = "python_jobs",
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        self.name = source_name or "python_jobs"
        self.timeout = int(timeout)

    def fetch_jobs(self) -> list[Job]:
        """GET the Python.org jobs page and parse job listings."""
        try:
            response = fetch_with_retry(
                _JOBS_URL,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — surface as zero results
            logger.warning(
                "Python.org Jobs source '{}' could not fetch {}: {}",
                self.name,
                _JOBS_URL,
                exc,
            )
            return []

        jobs = self._parse_html(response.text)
        logger.info(
            "Python.org Jobs source '{}' parsed {} job(s).",
            self.name,
            len(jobs),
        )
        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        """Parse the Python.org jobs listing page."""
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:  # noqa: BLE001
            return []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        # Python.org lists jobs as <li> inside <ol class="job-list">
        # or as <article> elements with class "job"
        job_items = soup.select("ol.job-listing > li") or soup.select("li.job")
        if not job_items:
            job_items = soup.find_all("li", class_=re.compile("job"))

        for item in job_items:
            # Title + URL from the heading link
            link = item.find("a", href=True)
            if not link:
                continue
            title = clean_text(link.get_text())
            url = link["href"]
            if not url.startswith("http"):
                url = f"https://www.python.org{url}"
            if url in seen:
                continue
            seen.add(url)

            # Company — often in a <span> or text after the title
            company = ""
            company_el = item.find("span", class_=re.compile("company|organization"))
            if company_el:
                company = clean_text(company_el.get_text())
            if not company:
                # Try text content after title
                full_text = clean_text(item.get_text())
                if title and title in full_text:
                    remainder = full_text.replace(title, "", 1).strip()
                    if remainder and len(remainder) < 200:
                        company = remainder.split(",")[0].strip()

            # Location
            location_el = item.find("span", class_=re.compile("location"))
            location = clean_text(location_el.get_text()) if location_el else None

            # Posted date
            time_el = item.find("time")
            published_at = None
            if time_el:
                published_at = time_el.get("datetime") or time_el.get("title")

            # Job type tags
            work_type = None
            type_el = item.find("span", class_=re.compile("type|tag"))
            if type_el:
                work_type = clean_text(type_el.get_text()) or None

            job = make_job(
                title=title,
                company=company or "Python.org",
                location=location,
                source=self.name,
                url=url,
                work_type=work_type,
                published_at=published_at,
                discovered_at=discovered_at,
            )
            if job:
                jobs.append(job)

        return jobs


__all__ = ["PythonJobsSource"]
