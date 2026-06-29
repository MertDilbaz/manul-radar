"""Hirex public career-page source.

Hirex is a careers product used by a handful of Turkish companies
(Papara, and potentially others). Public pages live at::

    https://app.gethirex.com/o/<slug>/

Two discovery paths are attempted:

1. **Static HTML anchors.** Some Hirex tenants render job cards
   server-side; we scan for anchors pointing at ``/o/<slug>/<job>``.
2. **Inline JSON scripts.** Others hydrate from a ``window.__NEXT_DATA__``
   or ``window.__INITIAL_STATE__`` blob, in which case the static HTML
   contains the slug but no per-job anchors. We regex-recover job URLs
   and titles from those blobs as a fallback.

If both paths yield nothing the source returns ``[]`` quietly. Per the
project decision (Hirex tenants often publish no open positions, or
hydrate client-side), an empty result is *not* an error and the
monitor logs it as info.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from app.models.job import Job
from app.sources.base_source import BaseSource
from app.sources.ats_helpers import (
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    clean_text,
)
from app.utils.logger import logger


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# Anchor pattern: /o/<slug>/<job-slug> or /jobs/<id>.
# Anchors like /o/<slug>/ alone are the listing page, not a job.
_JOB_ANCHOR_RE = re.compile(r"^/(?:o/[^/]+/|jobs/)[^/?#]+(?:[/?#]|$)")

# Inline JSON patterns that often contain job slugs.
_JOB_LINK_RE = re.compile(
    r'"(?:url|path|slug|link|href)"\s*:\s*"((?:/o/[^"/]+/|/jobs/)[^"/?#]+)"'
)
_JOB_TITLE_RE = re.compile(
    r'"(?:title|name|position|label)"\s*:\s*"([^"\\]{3,160})"'
)

_EMPTY_MARKERS: tuple[str, ...] = (
    "no open positions",
    "no published job postings found",
    "açık pozisyon bulunamadı",
    "şu anda açık pozisyon bulunmuyor",
)


class HirexSource(BaseSource):
    """Fetch open positions from any company's Hirex page.

    Args:
        company_name: Human-readable company name stamped on every
            emitted ``Job.company``. Required.
        careers_url: Absolute URL of the company's Hirex page. Required.
        slug: Hirex slug (the path segment after ``app.gethirex.com/o/``).
            When omitted, derived from ``careers_url``.
    """

    name: str = ""
    careers_url: str = ""
    company_name: str = ""
    slug: str = ""

    def __init__(
        self,
        company_name: str,
        careers_url: str,
        slug: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("HirexSource requires company_name")
        if not careers_url:
            raise ValueError("HirexSource requires careers_url")
        self.company_name = company_name
        self.careers_url = careers_url
        self.timeout = timeout
        if slug:
            self.slug = slug.strip().strip("/")
        else:
            self.slug = self._slug_from_url(careers_url)
        if not self.slug:
            raise ValueError(
                "HirexSource could not derive slug from "
                f"careers_url={careers_url!r}; pass slug= explicitly."
            )
        self.name = f"hirex_{self.slug}"

    @staticmethod
    def _slug_from_url(url: str) -> str:
        """Pull the segment after ``app.gethirex.com/o/``."""
        marker = "/o/"
        idx = url.find(marker)
        if idx < 0:
            return ""
        tail = url[idx + len(marker):]
        return tail.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()

    def fetch_jobs(self) -> list[Job]:
        """GET the page; ``[]`` if the page has no job anchors / JSON."""
        try:
            response = requests.get(
                self.careers_url,
                timeout=self.timeout,
                headers={**DEFAULT_HEADERS, "User-Agent": _USER_AGENT},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — surface as zero results
            logger.info(
                f"Hirex source '{self.name}' could not reach "
                f"{self.careers_url}: {exc}; returning []."
            )
            return []

        jobs = self._parse_jobs(response.text)
        if not jobs:
            logger.info(
                f"Hirex source '{self.name}' found 0 job(s) in static HTML; "
                "this is treated as a normal empty result, not an error."
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
                    f"Hirex page ({self.company_name}) reports empty "
                    f"state via marker {marker!r}; returning []."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Hirex HTML for {self.company_name} could not be parsed: {exc}"
            )
            return []

        # Path 1: visible anchors.
        anchor_jobs = self._extract_jobs_from_soup(soup)

        # Path 2: inline JSON hydration blobs. Always run, then merge.
        json_jobs = self._extract_jobs_from_scripts(html)

        merged = self._merge_unique(anchor_jobs, json_jobs)
        logger.info(
            f"Hirex source '{self.name}' parsed {len(merged)} job(s) "
            f"(anchors={len(anchor_jobs)}, scripts={len(json_jobs)})."
        )
        return merged

    # ---------------------- path 1: anchor scan ----------------------

    def _extract_jobs_from_soup(self, soup: BeautifulSoup) -> list[Job]:
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        discovered_at = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "").strip()
            if not _JOB_ANCHOR_RE.match(href.split("?", 1)[0]):
                continue
            absolute = urljoin(self.careers_url, href)
            # Skip the listing page itself.
            absolute_path = absolute.split("?", 1)[0].rstrip("/")
            listing_path = self.careers_url.split("?", 1)[0].rstrip("/")
            if absolute_path == listing_path:
                continue
            if absolute in seen_urls:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title or len(title) < 3:
                continue
            seen_urls.add(absolute)
            jobs.append(
                Job(
                    title=title[:160],
                    company=self.company_name,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=absolute,
                    description=None,
                    published_at=None,
                    discovered_at=discovered_at,
                )
            )
        return jobs

    # ---------------------- path 2: inline JSON ----------------------

    def _extract_jobs_from_scripts(self, html: str) -> list[Job]:
        """Recover job links + titles from embedded JSON blobs.

        This is best-effort. We never raise out of this method — if the
        blob is malformed or has no recognizable structure, we return
        ``[]`` and the source as a whole continues.
        """
        jobs: list[Job] = []
        discovered_at = datetime.utcnow().isoformat()

        for match in _JOB_LINK_RE.finditer(html):
            path = match.group(1)
            absolute = urljoin(self.careers_url, path)
            listing_path = self.careers_url.split("?", 1)[0].rstrip("/")
            if absolute.split("?", 1)[0].rstrip("/") == listing_path:
                continue
            title = self._title_near(html, match.start())
            jobs.append(
                Job(
                    title=title or f"Hirex Job {path.rsplit('/', 1)[-1]}",
                    company=self.company_name,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=absolute,
                    description=None,
                    published_at=None,
                    discovered_at=discovered_at,
                )
            )
        return jobs

    @staticmethod
    def _title_near(html: str, anchor_index: int) -> str:
        """Pick the closest plausible title token within ±400 chars."""
        window = html[max(0, anchor_index - 400): anchor_index + 400]
        match = _JOB_TITLE_RE.search(window)
        if not match:
            return ""
        raw = match.group(1)
        try:
            return clean_text(json.loads(f'"{raw}"'))
        except Exception:
            return clean_text(raw)

    # ---------------------- merge ----------------------

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


__all__ = ["HirexSource"]