"""LinkedIn Guest API job source.

LinkedIn does not expose a public Jobs API for third-party developers
without an approved partnership. However, the **guest-side** job search
endpoint returns JSON when accessed without authentication, which makes
it possible to read public job postings programmatically.

This source targets the guest API endpoint:

    https://www.linkedin.com/jobs-guest/api/jobPostings/jobs

It accepts ``keywords`` and ``location`` query parameters and returns
a JSON array of job posting summaries. No login, cookies, or OAuth
tokens are required. The endpoint is rate-limited and may return a
login wall / CAPTCHA after prolonged use; ``fetch_with_retry`` provides
basic resilience but this source is inherently fragile and may break
without notice if LinkedIn changes the guest API contract.

Limitations:
* The guest API returns at most ~25 jobs per request (paginated by
  the ``start`` parameter). This source fetches the first page only.
* Some fields (description, employment type) may be truncated or
  missing in the guest response.
* LinkedIn may block datacenter IPs (GitHub Actions runners) more
  aggressively than residential IPs. If the source consistently
  returns ``[]``, the IP is likely blocked.

If the guest API endpoint fails (403/429/HTML login wall), the source
falls back to the public search page and attempts to parse the
embedded JSON-LD / ```<script>``` data. This fallback is also fragile.
"""
from __future__ import annotations

import json
import re

from app.sources.ats_helpers import (
    JSON_HEADERS,
    REQUEST_TIMEOUT,
    fetch_with_retry,
    html_to_text,
    make_job,
    source_slug,
    utc_now_iso,
)
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger


_GUEST_API_BASE = "https://www.linkedin.com/jobs-guest/api/jobPostings/jobs"
_SEARCH_PAGE_BASE = "https://www.linkedin.com/jobs/search"

_LINKEDIN_HEADERS = {
    **JSON_HEADERS,
    "Accept-Language": "en-US,en;q=0.9",
}

# Embedded JSON pattern in LinkedIn search pages. LinkedIn injects
# job data inside <script> tags — sometimes as JSON-LD, sometimes
# as a JS variable assignment. We try the most common patterns.
_EMBEDDED_JSON_PATTERN = re.compile(
    r'"(jobPosting|jobPostings)"\s*:\s*(\[.*?\])\s*[,}]',
    re.DOTALL,
)


class LinkedInSource(BaseSource):
    """Fetch public job postings from LinkedIn's guest API.

    Args:
        keywords: Search query (e.g. ``"junior java developer"``).
        location: Location filter (e.g. ``"Turkey"``, ``"Istanbul, Turkey"``).
        source_name: Override for the ``name`` attribute.
        timeout: Network timeout in seconds.
    """

    def __init__(
        self,
        keywords: str,
        location: str = "Turkey",
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not keywords:
            raise ValueError("LinkedInSource requires non-empty keywords")
        self.keywords = keywords.strip()
        self.location = location.strip() if location else "Turkey"
        self.timeout = timeout
        self.name = source_name or source_slug("linkedin", f"{keywords}_{location}")
        # Build API URL with query params
        params = f"?keywords={self.keywords}&location={self.location}&start=0"
        self.api_url = f"{_GUEST_API_BASE}{params}"
        self.search_url = (
            f"{_SEARCH_PAGE_BASE}?keywords={self.keywords}&location={self.location}"
        )

    def fetch_jobs(self) -> list[Job]:
        """Fetch jobs via the guest API, falling back to HTML scraping."""
        try:
            jobs = self._fetch_via_guest_api()
            if jobs:
                return jobs
            logger.info(
                "LinkedIn guest API returned 0 jobs for '{}'; trying search page fallback.",
                self.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn guest API failed for '{}': {}; trying search page fallback.",
                self.name,
                exc,
            )

        try:
            return self._fetch_via_search_page()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn search page fallback also failed for '{}': {}; returning [].",
                self.name,
                exc,
            )
            return []

    def _fetch_via_guest_api(self) -> list[Job]:
        """Fetch from the guest API JSON endpoint."""
        response = fetch_with_retry(
            self.api_url,
            headers=_LINKEDIN_HEADERS,
            timeout=self.timeout,
        )

        # Guest API can return 200 with a login wall HTML instead of JSON.
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type:
            logger.debug(
                "LinkedIn guest API returned non-JSON Content-Type '{}' for '{}'; "
                "likely a login wall.",
                content_type,
                self.name,
            )
            return []

        data = response.json()
        if isinstance(data, dict):
            raw_jobs = data.get("jobs") or data.get("elements") or []
        elif isinstance(data, list):
            raw_jobs = data
        else:
            return []

        return self._parse_guest_api_jobs(raw_jobs)

    def _parse_guest_api_jobs(self, raw_jobs: list) -> list[Job]:
        """Parse the guest API JSON response into ``Job`` objects."""
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        for item in raw_jobs:
            if not isinstance(item, dict):
                continue

            title = item.get("title") or item.get("jobTitle") or ""
            if not title:
                continue

            # Company name can be nested under "companyName" or "company.name"
            company_name = (
                item.get("companyName")
                or (item.get("company") or {}).get("name")
                or (item.get("company") or {}).get("name")
                or ""
            )

            # Location
            location = (
                item.get("formattedLocation")
                or item.get("location")
                or (item.get("location") or {}).get("formattedLocation")
                or ""
            )

            # URL — LinkedIn job URLs
            url = (
                item.get("jobPostingUrl")
                or item.get("listedAt")
                or item.get("url")
                or ""
            )
            # If the URL is relative, make it absolute
            if url and not url.startswith("http"):
                url = f"https://www.linkedin.com{url}"

            if not url or url in seen:
                continue
            seen.add(url)

            # Description / employment status
            description_raw = ""
            desc_obj = item.get("description")
            if isinstance(desc_obj, dict):
                description_raw = desc_obj.get("text", "")
            elif isinstance(desc_obj, str):
                description_raw = desc_obj

            work_type = item.get("employmentStatus") or item.get("workType") or None
            published_at = item.get("listedAt") or item.get("postedAt") or None

            job = make_job(
                title=title,
                company=company_name or "Unknown",
                location=location or None,
                source=self.name,
                url=url,
                description=html_to_text(description_raw) or None,
                work_type=work_type,
                published_at=published_at,
                discovered_at=discovered_at,
            )
            if job:
                jobs.append(job)

        logger.info(
            "LinkedIn source '{}' parsed {} job(s) from guest API.",
            self.name,
            len(jobs),
        )
        return jobs

    def _fetch_via_search_page(self) -> list[Job]:
        """Fallback: fetch the public search page and parse embedded JSON."""
        response = fetch_with_retry(
            self.search_url,
            headers=_LINKEDIN_HEADERS,
            timeout=self.timeout,
        )
        html = response.text

        # Try to extract embedded JSON from the page
        match = _EMBEDDED_JSON_PATTERN.search(html)
        if not match:
            logger.debug(
                "LinkedIn search page for '{}' had no embedded job JSON.",
                self.name,
            )
            return []

        try:
            raw_jobs = json.loads(match.group(2))
        except json.JSONDecodeError:
            return []

        return self._parse_guest_api_jobs(raw_jobs)


__all__ = ["LinkedInSource"]
