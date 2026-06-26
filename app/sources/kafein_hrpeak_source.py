"""Kafein Technology Solutions HRPeak career-page source.

The first real source for Manul Radar: it GETs the public HRPeak
career page for Kafein Technology Solutions and turns whatever job
listings it can find into :class:`Job` instances. If the page is in
its "no open positions" state, it returns an empty list — that is
not an error condition, just a normal idle day.

The source splits into two concerns:

* :meth:`fetch_jobs` does the network I/O. Any HTTP / connection
  failure propagates up so :class:`JobMonitorService` can apply its
  source-level failure isolation.
* :meth:`_parse_jobs` is a pure string-to-``list[Job]`` transform.
  No network, no clock side effects — it is exercised directly by
  the smoke test against hand-crafted HTML samples.

This split lets us keep the parser brittle without taking the
service down with it: if the page changes shape, the parser logs a
warning and returns ``[]`` rather than raising.

Heuristics worth knowing:

* The page advertises its "no jobs" state with both a Turkish and an
  English marker. We bail out *before* parsing when we see either,
  because the empty-state markup can also contain anchors to other
  sections of the site that are not job postings.
* Job detail links are recognized by the URL containing ``ilan`` /
  ``job`` / ``position`` / ``kariyer``. The listing page itself
  (``site.aspx``) is explicitly excluded so we do not recurse into
  the page we are already on.
* Duplicate URLs are deduped via an in-memory set — HRPeak sometimes
  links the same posting twice (e.g. once in a list and once in a
  "featured" strip).
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.models.job import Job
from app.sources.base_source import BaseSource
from app.utils.logger import logger


# Anchors whose text or surrounding DOM we cannot trust to be a job
# title are skipped — keeps the parser from emitting garbage rows
# when the markup shifts under us.
_MIN_TITLE_LEN = 3

# Markers the page uses to signal "we have no open positions right
# now". We short-circuit on these so we don't accidentally pick up
# navigation anchors as phantom job postings.
_EMPTY_MARKERS: tuple[str, ...] = (
    "yayınlanmış bir açık pozisyon bulunamadı",
    "şu anda açık pozisyon bulunmuyor",
    "no published job postings found",
    "no open positions",
)

# Tokens that suggest a URL points to a job-detail page. Anything
# else is treated as navigation and skipped.
_JOB_LINK_TOKENS: tuple[str, ...] = (
    "ilan",
    "job",
    "position",
    "kariyer",
)

# Listing-page URL we want to exclude from job-link matches so we
# do not "discover" the listing page as its own job.
_LISTING_URL_SUFFIXES: tuple[str, ...] = (
    "/site.aspx",
    "/ilan/site.aspx",
)

_COMPANY_NAME = "Kafein Technology Solutions"

_USER_AGENT = (
    "Mozilla/5.0 (compatible; ManulRadar/1.0; "
    "+https://github.com/local/manul-radar)"
)


class KafeinHrPeakSource(BaseSource):
    """Fetch open positions from Kafein's HRPeak career page."""

    name: str = "kafein_hrpeak"
    BASE_URL: str = "https://kafein.hrpeak.com/ilan/site.aspx"
    REQUEST_TIMEOUT: int = 15

    def fetch_jobs(self) -> list[Job]:
        """GET the page and parse it into a list of ``Job`` instances.

        Raises:
            requests.exceptions.RequestException: On any network or
                HTTP failure. ``JobMonitorService`` catches and logs
                these at the source boundary.
        """
        headers = {"User-Agent": _USER_AGENT}
        response = requests.get(
            self.BASE_URL,
            timeout=self.REQUEST_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    # ---------------------- pure parsing (no I/O) ----------------------

    @staticmethod
    def _parse_jobs(html: str) -> list[Job]:
        """Turn a page's HTML into ``Job`` instances; ``[]`` if empty / parse failure.

        This method is deliberately pure so the smoke test can call it
        with hand-crafted HTML without needing the network. The empty
        marker check runs first so we never call into BeautifulSoup on
        the "no jobs" page; the BeautifulSoup call is wrapped in a
        broad except so a hostile or malformed page yields an empty
        list rather than a crash.
        """
        if not html:
            return []

        lowered = html.lower()
        for marker in _EMPTY_MARKERS:
            if marker in lowered:
                logger.debug(
                    "Kafein page reports empty state via marker "
                    f"{marker!r}; returning []."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001 — defensive against parser regressions
            logger.warning(
                f"Kafein HTML could not be parsed; returning []: {exc}"
            )
            return []

        return KafeinHrPeakSource._extract_jobs_from_soup(soup)

    @staticmethod
    def _extract_jobs_from_soup(soup: BeautifulSoup) -> list[Job]:
        """Walk every anchor and emit a ``Job`` for those that look like listings."""
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        now = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            absolute = urljoin(KafeinHrPeakSource.BASE_URL, href)
            if not KafeinHrPeakSource._looks_like_job_link(absolute):
                continue
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)

            title = KafeinHrPeakSource._extract_title(anchor)
            if len(title) < _MIN_TITLE_LEN:
                # Could not find a usable title — treat as junk and skip
                # rather than emitting a Job with a junk title.
                continue

            jobs.append(
                Job(
                    title=title,
                    company=_COMPANY_NAME,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=KafeinHrPeakSource.name,
                    url=absolute,
                    description=None,
                    published_at=None,
                    discovered_at=now,
                )
            )

        return jobs

    @staticmethod
    def _looks_like_job_link(url: str) -> bool:
        """``True`` iff ``url`` looks like a job-detail page link."""
        lowered = url.lower()
        if any(lowered.endswith(suffix) for suffix in _LISTING_URL_SUFFIXES):
            return False
        return any(token in lowered for token in _JOB_LINK_TOKENS)

    @staticmethod
    def _extract_title(anchor) -> str:
        """Best-effort title for a job anchor.

        Tries the anchor's own text first (``get_text`` recurses into
        nested tags so ``<a><strong>Title</strong></a>`` still works),
        then the ``title`` attribute. We deliberately do **not** fall
        back to ``anchor.parent.get_text`` because that walks the whole
        ancestor subtree and produces noisy concatenations like
        ``"abReal Job Posting"`` when two anchors share an ancestor.
        Anything shorter than ``_MIN_TITLE_LEN`` returns ``""`` and
        the caller skips the link.
        """
        text = anchor.get_text(strip=True)
        if len(text) >= _MIN_TITLE_LEN:
            return text

        title_attr = (anchor.get("title") or "").strip()
        if len(title_attr) >= _MIN_TITLE_LEN:
            return title_attr

        return ""


__all__ = ["KafeinHrPeakSource"]
