"""Generic HRPeak career-page source.

HRPeak is an HR / job-board product used by multiple Turkish companies
(Kafein Technology Solutions, and potentially others in the future).
The listing page layout, "no open positions" markers, and the
convention that job-detail URLs contain ``ilan`` / ``job`` /
``position`` / ``kariyer`` are all HRPeak-side, not company-side —
so a single parser can be reused across companies simply by passing
in a different ``careers_url``.

The V0.2 refactor: this module replaces the Kafein-specific
``kafein_hrpeak_source.py`` and now accepts ``company_name`` and
``careers_url`` via the constructor. Behaviour (network, HTML
parsing, empty-detection, dedup, URL normalization) is identical to
the old Kafein-only source so existing test fixtures keep working.

Configuration:

* ``run_monitor.py`` reads ``config.sources`` and instantiates one
  ``HrPeakSource`` per entry whose ``parser`` key is ``"hrpeak"``.
* Adding a new HRPeak customer is now a config-only change:

      sources:
        - parser: hrpeak
          company: Some Other Co
          url: https://someother.hrpeak.com/ilan/site.aspx
          enabled: true

Failures:
* Network / HTTP errors propagate up via
  ``requests.Response.raise_for_status`` and the underlying
  ``requests`` exceptions. ``JobMonitorService`` catches and logs
  these at the source boundary.
* Malformed HTML yields ``[]`` rather than raising — the parser is
  allowed to be brittle as long as it does not take the service
  down with it.
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
# do not "discover" the listing page as its own job. The match is
# suffix-based so any company that points ``careers_url`` at a
# different listing path still gets excluded if the path ends with
# one of these. ``/jobs`` covers the newer HRPeak tenants (e.g.
# ziraatteknoloji.hrpeak.com/jobs, innova.hrpeak.com/jobs); their
# actual job-detail URLs end with ``/jobs/<id>`` so they still pass.
_LISTING_URL_SUFFIXES: tuple[str, ...] = (
    "/site.aspx",
    "/ilan/site.aspx",
    "/jobs",
    "/jobs/",
)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; ManulRadar/1.0; "
    "+https://github.com/local/manul-radar)"
)

REQUEST_TIMEOUT: int = 15


class HrPeakSource(BaseSource):
    """Fetch open positions from any company's HRPeak career page.

    Args:
        company_name: Human-readable company name stamped on every
            emitted ``Job.company``. Required.
        careers_url: Absolute URL of the company's HRPeak listing
            page. Required; used both as the GET target and as the
            base for resolving relative ``href``s.

    The class attribute ``name`` is derived from ``company_name``
    (slugified, lowercased) so two HRPeak customers in the same
    run can coexist without collision in logs, persistence keys,
    and notification payloads. If a future config field needs to
    pin the source name explicitly, that hook belongs here.
    """

    name: str = ""
    careers_url: str = ""
    company_name: str = ""

    def __init__(self, company_name: str, careers_url: str) -> None:
        """Store the company name and careers URL.

        Both are required; we do not silently fall back to a
        default because that would re-introduce the hardcoded
        "Kafein Technology Solutions" coupling the V0.2 refactor
        exists to remove.
        """
        if not company_name:
            raise ValueError(
                "HrPeakSource requires a non-empty company_name"
            )
        if not careers_url:
            raise ValueError(
                "HrPeakSource requires a non-empty careers_url"
            )
        self.company_name = company_name
        self.careers_url = careers_url
        self.name = self._derive_source_name(company_name)

    @staticmethod
    def _derive_source_name(company_name: str) -> str:
        """Turn a company name into a stable, log-friendly source id.

        Examples::

            "Kafein Technology Solutions" -> "kafein_technology_solutions"
            "Foo-Bar.co" -> "foo_bar_co"

        The transformation is intentionally simple: lowercase,
        alphanumerics kept, the rest become underscores, and
        leading / trailing underscores are stripped. It does not
        need to be unique across companies — the source list is
        configured by the operator, and per-company name clashes
        would be a config bug to surface, not to paper over.
        """
        lowered = company_name.lower()
        cleaned: list[str] = []
        prev_underscore = False
        for ch in lowered:
            if ch.isalnum():
                cleaned.append(ch)
                prev_underscore = False
            else:
                if not prev_underscore:
                    cleaned.append("_")
                    prev_underscore = True
        return "".join(cleaned).strip("_")

    def fetch_jobs(self) -> list[Job]:
        """GET the page and parse it into a list of ``Job`` instances.

        Raises:
            requests.exceptions.RequestException: On any network or
                HTTP failure. ``JobMonitorService`` catches and logs
                these at the source boundary.
        """
        headers = {"User-Agent": _USER_AGENT}
        response = requests.get(
            self.careers_url,
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    # ---------------------- pure parsing (no I/O) ----------------------

    def _parse_jobs(self, html: str) -> list[Job]:
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
                    f"HRPeak page ({self.company_name}) reports empty "
                    f"state via marker {marker!r}; returning []."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001 — defensive against parser regressions
            logger.warning(
                f"HRPeak HTML for {self.company_name} could not be "
                f"parsed; returning []: {exc}"
            )
            return []

        return self._extract_jobs_from_soup(soup)

    def _extract_jobs_from_soup(self, soup: BeautifulSoup) -> list[Job]:
        """Walk every anchor and emit a ``Job`` for those that look like listings."""
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        now = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            absolute = urljoin(self.careers_url, href)
            if not self._looks_like_job_link(absolute):
                continue
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)

            title = self._extract_title(anchor)
            if len(title) < _MIN_TITLE_LEN:
                # Could not find a usable title — treat as junk and skip
                # rather than emitting a Job with a junk title.
                continue

            jobs.append(
                Job(
                    title=title,
                    company=self.company_name,
                    location=None,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=absolute,
                    description=None,
                    published_at=None,
                    discovered_at=now,
                )
            )

        return jobs

    def _looks_like_job_link(self, url: str) -> bool:
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


__all__ = ["HrPeakSource"]
