"""Zoho Recruit public career-page source.

Zoho Recruit hosts public career pages at::

    https://<tenant>.zohorecruit.com/jobs/<PortalName>

Many tenants either:

* render job cards server-side as anchors pointing at
  ``/recruit/v2/ViewJobPosting?jobId=<id>`` or a portal-prefixed
  ``/jobs/<PortalName>/<job-id>`` path; or
* hydrate job listings from the public ``/recruit/v2/PublishedJobPostings``
  REST API which usually requires an authentication token we don't
  have access to (returns 401).

This source is therefore a *best-effort HTML parser*:

1. We GET the public listing page.
2. We extract every anchor whose URL looks like a Zoho Recruit
   job-detail page.
3. We extract inline JSON blobs (e.g. ``window.__INITIAL_STATE__``)
   as a fallback for hydration-heavy tenants.
4. If neither path yields anything, we return ``[]`` quietly — empty
   pages and authenticated-only APIs are expected outcomes here, not
   errors.

The source is currently registered as ``enabled: false`` in
``companies.yaml`` (with ``disabled_reason``) until a live tenant
is verified to round-trip a parseable URL; see
``docs/PLAN_next_sources.md`` (Sprint 3 status).
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.models.job import Job
from app.sources.base_source import BaseSource
from app.sources.ats_helpers import (
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    clean_text,
    utc_now_iso,
    fetch_with_retry,
)
from app.utils.logger import logger


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# Job-detail anchor patterns. Both shapes are seen across tenants.
_VIEW_JOB_RE = re.compile(r"/recruit/v2/ViewJobPosting", re.IGNORECASE)
_PORTAL_JOB_RE = re.compile(
    r"^/jobs/[^/?#]+/[^/?#]+(?:[/?#]|$)", re.IGNORECASE
)

# Inline JSON regex patterns.
_INLINE_JOB_RE = re.compile(
    r'"(?:jobId|job_id|id)"\s*:\s*"([^"\\]{3,64})"'
)
_INLINE_TITLE_RE = re.compile(
    r'"(?:JobTitle|job_title|title|name|position)"\s*:\s*"([^"\\]{3,160})"'
)

_EMPTY_MARKERS: tuple[str, ...] = (
    "no job postings",
    "no open positions",
    "no published job postings found",
    "şu anda açık pozisyon bulunmuyor",
    "açık pozisyon bulunamadı",
)

# Tokens to skip when scanning anchors (CSS, JS, image, mailto, etc.).
_SKIP_HREF_TOKENS: tuple[str, ...] = (
    "javascript:",
    "mailto:",
    "tel:",
    "#",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".svg",
    ".ico",
    "static.zohocdn.com",
)


def _is_job_anchor(href: str, listing_path: str) -> bool:
    """``True`` iff ``href`` looks like a Zoho Recruit job-detail URL."""
    if not href:
        return False
    lowered = href.lower()
    if any(token in lowered for token in _SKIP_HREF_TOKENS):
        return False

    if _VIEW_JOB_RE.search(lowered):
        return True

    parsed_path = urlparse(href).path
    if parsed_path and _PORTAL_JOB_RE.match(parsed_path):
        # Exclude the listing page itself.
        if parsed_path.rstrip("/").lower() == listing_path.rstrip("/").lower():
            return False
        return True

    # Some tenants render ?jobId=... query strings on otherwise-blank paths.
    query = parse_qs(urlparse(href).query)
    if any(key.lower() in {"jobid", "job_id", "jobpostingid"} for key in query):
        return True

    return False


def _build_job_url(base_url: str, href: str, portal_name: str) -> str:
    """Turn a Zoho Recruit job-detail href into a canonical absolute URL."""
    from urllib.parse import urljoin
    parsed_href = urlparse(href)
    query = parse_qs(parsed_href.query)
    job_id = None
    for key in ("jobId", "job_id", "jobPostingId"):
        if query.get(key):
            job_id = query[key][0]
            break
    if job_id:
        # Drop the listing-path prefix (/jobs/<PortalName>) — the
        # ViewJobPosting endpoint lives at the tenant root.
        origin = urlparse(base_url)
        return (
            f"{origin.scheme}://{origin.netloc}"
            f"/recruit/v2/ViewJobPosting?jobId={job_id}"
        )
    # Anchor path like /jobs/<PortalName>/<job-slug>
    if parsed_href.path and _PORTAL_JOB_RE.match(parsed_href.path):
        return urljoin(base_url, parsed_href.path)
    # Fall back to the original href against the listing origin.
    return urljoin(base_url, href)


class ZohoRecruitSource(BaseSource):
    """Fetch open positions from a Zoho Recruit public career page.

    Args:
        company_name: Human-readable company name stamped on every
            emitted ``Job.company``. Required.
        careers_url: Absolute URL of the company's public Zoho
            Recruit listing page. Required.
        portal_name: Zoho Recruit portal slug (the path segment
            after ``/jobs/``). When omitted, derived from
            ``careers_url``. Required for URL normalization.
    """

    name: str = ""
    careers_url: str = ""
    company_name: str = ""
    portal_name: str = ""

    def __init__(
        self,
        company_name: str,
        careers_url: str,
        portal_name: str | None = None,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("ZohoRecruitSource requires company_name")
        if not careers_url:
            raise ValueError("ZohoRecruitSource requires careers_url")
        self.company_name = company_name
        self.careers_url = careers_url
        self.timeout = timeout
        if portal_name:
            self.portal_name = portal_name.strip().strip("/")
        else:
            self.portal_name = self._portal_from_url(careers_url)
        if not self.portal_name:
            raise ValueError(
                "ZohoRecruitSource could not derive portal_name from "
                f"careers_url={careers_url!r}; pass portal_name= explicitly."
            )
        if source_name:
            self.name = source_name
            return
        # Slugify portal_name for source id.
        slug = re.sub(r"[^a-z0-9]+", "_", self.portal_name.lower()).strip("_")
        self.name = f"zoho_recruit_{slug}" if slug else "zoho_recruit"

    @staticmethod
    def _portal_from_url(url: str) -> str:
        """Pull the path segment after ``/jobs/``."""
        marker = "/jobs/"
        idx = url.find(marker)
        if idx < 0:
            return ""
        tail = url[idx + len(marker):]
        return tail.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()

    def fetch_jobs(self) -> list[Job]:
        """GET the public page; ``[]`` if no job URLs are found.

        Empty results are *not* an error — many Zoho Recruit tenants
        either publish no open positions or hydrate listings from an
        authenticated API endpoint we can't call.
        """
        try:
            response = fetch_with_retry(
                self.careers_url,
                timeout=self.timeout,
                headers={**DEFAULT_HEADERS, "User-Agent": _USER_AGENT},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.info(
                f"ZohoRecruit source '{self.name}' could not reach "
                f"{self.careers_url}: {exc}; returning []."
            )
            return []

        jobs = self._parse_jobs(response.text)
        if not jobs:
            logger.info(
                f"ZohoRecruit source '{self.name}' found 0 job(s); "
                "tenant may be auth-gated or hydration-only. "
                "Treated as a normal empty result, not an error."
            )
        return jobs

    # ---------------------- pure parsing (no I/O) ----------------------

    def _parse_jobs(self, html: str) -> list[Job]:
        if not html:
            return []

        lowered = html.lower()
        for marker in _EMPTY_MARKERS:
            if marker in lowered:
                logger.debug(
                    f"ZohoRecruit page ({self.company_name}) reports empty "
                    f"state via marker {marker!r}; returning []."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"ZohoRecruit HTML for {self.company_name} could not be "
                f"parsed: {exc}"
            )
            return []

        listing_path = urlparse(self.careers_url).path

        anchor_jobs = self._extract_jobs_from_soup(soup, listing_path)
        script_jobs = self._extract_jobs_from_scripts(html)
        merged = self._merge_unique(anchor_jobs, script_jobs)

        logger.info(
            f"ZohoRecruit source '{self.name}' parsed {len(merged)} job(s) "
            f"(anchors={len(anchor_jobs)}, scripts={len(script_jobs)})."
        )
        return merged

    def _extract_jobs_from_soup(
        self,
        soup: BeautifulSoup,
        listing_path: str,
    ) -> list[Job]:
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        discovered_at = utc_now_iso()

        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "").strip()
            if not _is_job_anchor(href, listing_path):
                continue

            url = _build_job_url(self.careers_url, href, self.portal_name)
            if url in seen_urls:
                continue

            title = clean_text(anchor.get_text(" ", strip=True))
            if not title or len(title) < 3:
                continue

            seen_urls.add(url)
            jobs.append(
                Job(
                    title=title[:160],
                    company=self.company_name,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=url,
                    description=None,
                    published_at=None,
                    discovered_at=discovered_at,
                )
            )
        return jobs

    def _extract_jobs_from_scripts(self, html: str) -> list[Job]:
        """Recover job ids + titles from inline JSON blobs.

        Best-effort. We synthesize a stable ``/recruit/v2/ViewJobPosting?jobId=...``
        URL for each jobId we find; titles are picked from the nearest
        JSON title token within ±400 chars.
        """
        jobs: list[Job] = []
        seen_ids: set[str] = set()
        discovered_at = utc_now_iso()

        for match in _INLINE_JOB_RE.finditer(html):
            job_id = match.group(1)
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            url = (
                f"{self.careers_url.rstrip('/')}"
                f"/recruit/v2/ViewJobPosting?jobId={job_id}"
            )
            title = self._title_near(html, match.start())
            jobs.append(
                Job(
                    title=title or f"Zoho Recruit Job {job_id}",
                    company=self.company_name,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=url,
                    description=None,
                    published_at=None,
                    discovered_at=discovered_at,
                )
            )
        return jobs

    @staticmethod
    def _title_near(html: str, anchor_index: int) -> str:
        window = html[max(0, anchor_index - 400): anchor_index + 400]
        match = _INLINE_TITLE_RE.search(window)
        if not match:
            return ""
        raw = match.group(1)
        try:
            return clean_text(json.loads(f'"{raw}"'))
        except Exception:
            return clean_text(raw)

    @staticmethod
    def _merge_unique(*job_lists: list[Job]) -> list[Job]:
        seen: set[str] = set()
        merged: list[Job] = []
        for jobs in job_lists:
            for job in jobs:
                if job.url in seen:
                    continue
                seen.add(job.url)
                merged.append(job)
        return merged


__all__ = ["ZohoRecruitSource"]