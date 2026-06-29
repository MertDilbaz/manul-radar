"""LinkedIn Guest API job search source.

This source fetches job postings from LinkedIn's **public guest-side**
job search endpoint. It does *not* require login, cookies, Playwright,
or any browser automation — it issues plain HTTP GETs with a desktop
User-Agent.

The primary endpoint is::

    https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search

This endpoint returns **HTML fragments** (not JSON) — a list of ``<li>``
elements, each containing a job card with title, company, location, and
URL. We parse these with BeautifulSoup, the same approach used for
HRPeak and Kariyer.net.

If the guest endpoint is blocked (LinkedIn occasionally returns a 999
status or login redirect), the source falls back to the regular search
results page and extracts the embedded JSON.

Experience level filter (``f_E``) is supported to focus on entry-level
roles: ``2`` = Entry, ``3`` = Associate, ``1`` = Internship.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.sources.ats_helpers import (
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    clean_text,
    fetch_with_retry,
    html_to_text,
    make_job,
    source_slug,
    utc_now_iso,
)
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger

#: Headers tuned for LinkedIn's guest endpoints.
LINKEDIN_HEADERS = {
    **DEFAULT_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_GUEST_API_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?keywords={keywords}&location={location}&start=0{exp_filter}"
)
_SEARCH_PAGE_URL = (
    "https://www.linkedin.com/jobs/search/?keywords={keywords}&location={location}"
)

#: LinkedIn returns HTTP 999 (non-standard) when it decides the request
#: looks bot-like. Treat it as a soft failure.
_LINKEDIN_SOFT_FAIL_STATUSES = frozenset({999})

#: Experience level codes for the f_E parameter.
_EXP_LEVELS = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid_senior": "4",
}


class LinkedInSource(BaseSource):
    """Fetch jobs from LinkedIn's public guest-side search endpoints.

    Args:
        keywords: Search query (e.g. ``"junior java developer"``).
        location: Location filter (e.g. ``"Turkey"``, ``"Istanbul"``).
        experience_level: Optional filter: ``"entry"``, ``"associate"``,
            ``"internship"``, or ``None`` for no filter.
        source_name: Override for the ``name`` attribute.
        timeout: Network timeout in seconds.
    """

    def __init__(
        self,
        keywords: str,
        location: str = "Turkey",
        experience_level: str | None = None,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not keywords:
            raise ValueError("LinkedInSource requires keywords")
        self.keywords = keywords.strip()
        self.location = location.strip() if location else "Turkey"
        self.timeout = timeout
        self.name = source_name or source_slug("linkedin", f"{keywords}_{location}")

        exp_code = _EXP_LEVELS.get((experience_level or "").lower().strip(), "")
        exp_filter = f"&f_E={exp_code}" if exp_code else ""
        self.api_url = _GUEST_API_URL.format(
            keywords=quote_plus(self.keywords),
            location=quote_plus(self.location),
            exp_filter=exp_filter,
        )
        self.search_url = _SEARCH_PAGE_URL.format(
            keywords=quote_plus(self.keywords),
            location=quote_plus(self.location),
        )

    def fetch_jobs(self) -> list[Job]:
        """Fetch jobs via the guest API, falling back to search page."""
        try:
            jobs = self._fetch_via_guest_api()
            if jobs:
                return jobs
            logger.info(
                "LinkedIn source '{}' returned 0 jobs from guest API; "
                "trying search page fallback.",
                self.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn guest API failed for '{}': {}; trying fallback.",
                self.name,
                exc,
            )

        try:
            return self._fetch_via_search_page()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LinkedIn search page fallback failed for '{}': {}; returning [].",
                self.name,
                exc,
            )
            return []

    def _fetch_via_guest_api(self) -> list[Job]:
        """Fetch from the guest endpoint and parse HTML job cards."""
        response = fetch_with_retry(
            self.api_url,
            headers=LINKEDIN_HEADERS,
            timeout=self.timeout,
        )

        if response.status_code in _LINKEDIN_SOFT_FAIL_STATUSES:
            logger.debug(
                "LinkedIn guest API returned {} for '{}'; soft fail.",
                response.status_code,
                self.name,
            )
            return []

        # The seeMoreJobPostings endpoint returns HTML fragments
        # (a list of <li> elements), not JSON.
        if not response.text or len(response.text) < 100:
            return []

        return self._parse_html_cards(response.text)

    def _parse_html_cards(self, html: str) -> list[Job]:
        """Parse LinkedIn's HTML job card fragments into ``Job`` objects.

        Each job is wrapped in a ``<li>`` containing a
        ``.base-search-card`` div with:
        - ``.base-search-card__title`` → title
        - ``.base-search-card__subtitle`` (or ``.job-search-card__subtitle-link``) → company
        - ``.job-search-card__location`` → location
        - ``a.base-card__full-link`` → URL (href)
        - ``.job-search-card__list-item`` → metadata (posted date, etc.)
        """
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:  # noqa: BLE001
            return []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        # Each job card is an <li> or a div with base-search-card class
        cards = soup.find_all("li", class_=re.compile("jobs-search-results__list-item"))
        if not cards:
            cards = soup.find_all("div", class_=re.compile("base-search-card"))

        for card in cards:
            # Title
            title_el = card.find(
                "h3", class_=re.compile("base-search-card__title")
            ) or card.find("h3")
            title = clean_text(title_el.get_text()) if title_el else ""
            if not title:
                continue

            # Company
            company_el = card.find(
                "h4", class_=re.compile("base-search-card__subtitle")
            ) or card.find("a", class_=re.compile("subtitle-link"))
            company = clean_text(company_el.get_text()) if company_el else ""
            if not company:
                company = "Unknown"

            # Location
            location_el = card.find(
                "span", class_=re.compile("job-search-card__location")
            ) or card.find("span", class_=re.compile("location"))
            location = clean_text(location_el.get_text()) if location_el else None

            # URL — look for the main anchor
            link_el = card.find(
                "a", class_=re.compile("base-card__full-link")
            ) or card.find("a", href=re.compile("/jobs/view/"))
            url = ""
            if link_el and link_el.get("href"):
                url = link_el["href"].split("?")[0]  # strip tracking params
            if not url or url in seen:
                continue
            seen.add(url)

            # Posted date
            time_el = card.find("time")
            published_at = None
            if time_el:
                published_at = time_el.get("datetime") or time_el.get("title")

            # Work type / employment status — sometimes in metadata list
            work_type = None
            metadata_items = card.find_all(
                "li", class_=re.compile("job-search-card__list-item")
            )
            for item in metadata_items:
                text = clean_text(item.get_text())
                if any(
                    kw in text.lower()
                    for kw in ("full-time", "part-time", "contract", "temporary", "internship")
                ):
                    work_type = text
                    break

            job = make_job(
                title=title,
                company=company,
                location=location,
                source=self.name,
                url=url,
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
            headers=LINKEDIN_HEADERS,
            timeout=self.timeout,
        )
        html = response.text

        # Try to extract embedded JSON from the page
        # LinkedIn sometimes embeds data in <code> tags
        code_tags = BeautifulSoup(html, "html.parser").find_all("code")
        for tag in code_tags:
            text = tag.get_text()
            if '"jobPosting"' in text or '"job_posting"' in text or '"data"' in text:
                try:
                    data = json.loads(text)
                    raw_jobs = self._extract_jobs_from_json(data)
                    if raw_jobs:
                        return self._parse_json_jobs(raw_jobs)
                except json.JSONDecodeError:
                    continue

        return []

    def _extract_jobs_from_json(self, data: object) -> list[dict]:
        """Recursively search LinkedIn's embedded JSON for job objects."""
        results: list[dict] = []

        def _search(obj):
            if isinstance(obj, dict):
                # Check if this looks like a job posting
                if "title" in obj and ("companyName" in obj or "company" in obj):
                    results.append(obj)
                for value in obj.values():
                    _search(value)
            elif isinstance(obj, list):
                for item in obj:
                    _search(item)

        _search(data)
        return results

    def _parse_json_jobs(self, raw_jobs: list[dict]) -> list[Job]:
        """Parse job objects extracted from embedded JSON."""
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        for item in raw_jobs:
            title = item.get("title") or ""
            if not title:
                continue

            company = (
                item.get("companyName")
                or (item.get("company") or {}).get("name")
                or ""
            )
            location = item.get("formattedLocation") or item.get("location") or ""
            url = item.get("jobPostingUrl") or item.get("url") or ""
            if url and not url.startswith("http"):
                url = f"https://www.linkedin.com{url}"
            if not url or url in seen:
                continue
            seen.add(url)

            job = make_job(
                title=title,
                company=company or "Unknown",
                location=location or None,
                source=self.name,
                url=url,
                published_at=item.get("listedAt") or item.get("postedAt"),
                discovered_at=discovered_at,
            )
            if job:
                jobs.append(job)

        logger.info(
            "LinkedIn source '{}' parsed {} job(s) from search page JSON.",
            self.name,
            len(jobs),
        )
        return jobs


__all__ = ["LinkedInSource"]
