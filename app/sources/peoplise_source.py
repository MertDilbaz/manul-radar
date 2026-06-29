"""Peoplise public career-page source.

Peoplise is a Turkish HR / careers product used by multiple companies
(Logo Yazılım, and potentially others). Career pages live at::

    https://live.peoplise.com/<account>/career

A *job* is any anchor whose path matches the form
``/application/landing/<uuid>``. Other anchors (social, footer, legal,
``javascript:void(0)``) are noise and are skipped.

The V0.2 refactor: this module mirrors the generic-source pattern used
elsewhere (HRPeak, SuccessFactors). The only job-link token is
``/application/landing/`` per the project decision; titles and locations
are parsed out of the anchor's own text, which is the most stable
surface across the few Peoplise tenants in scope.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

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

# Job-detail anchor pattern. Per project decision, *only* these URLs
# count as a job link. Anything else (social, footer, legal, void)
# is treated as chrome.
_LANDING_PATH_RE = re.compile(r"^/[^/]+/application/landing/[^/?#]+/?$")

# Markers the page uses to signal "no open positions". We short-circuit
# on these so we don't accidentally pick up navigation anchors.
_EMPTY_MARKERS: tuple[str, ...] = (
    "açık pozisyon bulunamadı",
    "şu anda açık pozisyon bulunmuyor",
    "no open positions",
    "no published job postings found",
)

# Anchor texts to skip even when they happen to look like jobs (they
# never do here, but defensive).
_SKIP_TITLE_TOKENS: tuple[str, ...] = (
    "apply now",
    "başvur",
    "basvur",
    "view job",
    "share",
)


def _looks_like_landing_url(href: str, account: str) -> bool:
    """``True`` iff ``href`` is ``/<account>/application/landing/<id>``.

    Other paths on the same origin (homepage, social, login) are
    rejected even if they happen to contain the substring ``application``.
    Accepts both absolute (``https://live.peoplise.com/...``) and
    relative (``/logo/application/landing/...``) hrefs — only the
    path segment is matched.
    """
    if not href:
        return False
    parsed_path = urlparse(href).path
    if not parsed_path.startswith("/"):
        return False
    return bool(_LANDING_PATH_RE.match(parsed_path)) and (
        parsed_path.startswith(f"/{account}/")
    )


class PeopliseSource(BaseSource):
    """Fetch open positions from any company's Peoplise career page.

    Args:
        company_name: Human-readable company name stamped on every
            emitted ``Job.company``. Required.
        careers_url: Absolute URL of the company's Peoplise listing
            page. Required.
        account: Peoplise account slug (the path segment after
            ``live.peoplise.com/``). When omitted, derived from
            ``careers_url``. Required for job-link filtering.
    """

    name: str = ""
    careers_url: str = ""
    company_name: str = ""
    account: str = ""

    def __init__(
        self,
        company_name: str,
        careers_url: str,
        account: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("PeopliseSource requires company_name")
        if not careers_url:
            raise ValueError("PeopliseSource requires careers_url")
        self.company_name = company_name
        self.careers_url = careers_url
        self.timeout = timeout
        if account:
            self.account = account.strip().strip("/")
        else:
            self.account = self._account_from_url(careers_url)
        if not self.account:
            raise ValueError(
                "PeopliseSource could not derive account from "
                f"careers_url={careers_url!r}; pass account= explicitly."
            )
        self.name = f"peoplise_{self.account}"

    @staticmethod
    def _account_from_url(url: str) -> str:
        """Pull the path segment after ``live.peoplise.com/``."""
        # Naive but sufficient for the canonical URL shape.
        marker = "live.peoplise.com/"
        idx = url.find(marker)
        if idx < 0:
            return ""
        tail = url[idx + len(marker):]
        return tail.split("/", 1)[0].strip()

    def fetch_jobs(self) -> list[Job]:
        """GET the page and parse it into ``Job`` instances."""
        response = requests.get(
            self.careers_url,
            timeout=self.timeout,
            headers={**DEFAULT_HEADERS, "User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    # ---------------------- pure parsing (no I/O) ----------------------

    def _parse_jobs(self, html: str) -> list[Job]:
        """Turn a page's HTML into ``Job`` instances; ``[]`` if empty."""
        if not html:
            return []

        lowered = html.lower()
        for marker in _EMPTY_MARKERS:
            if marker in lowered:
                logger.debug(
                    f"Peoplise page ({self.company_name}) reports empty "
                    f"state via marker {marker!r}; returning []."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001 — defensive parser boundary
            logger.warning(
                f"Peoplise HTML for {self.company_name} could not be "
                f"parsed; returning []: {exc}"
            )
            return []

        return self._extract_jobs_from_soup(soup)

    def _extract_jobs_from_soup(self, soup: BeautifulSoup) -> list[Job]:
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        from datetime import datetime  # local import keeps the module cheap
        discovered_at = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "").strip()
            if not _looks_like_landing_url(href, self.account):
                continue

            absolute = urljoin(self.careers_url, href)
            if absolute in seen_urls:
                continue

            title = self._extract_title(anchor)
            if not title:
                continue

            seen_urls.add(absolute)
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
                    discovered_at=discovered_at,
                )
            )

        logger.info(
            f"Peoplise source '{self.name}' parsed {len(jobs)} job(s)."
        )
        return jobs

    @staticmethod
    def _extract_title(anchor: Tag) -> str:
        """Pull the most plausible job title out of an anchor.

        Peoplise renders the role and the location in the same anchor
        text — e.g. ``"Kıdemli Proje Çözüm Geliştirme Uzmanı Kocaeli(Gebze)
        Son Başvuru Tarihi : 19.12."``. We split on common location
        tokens; if that fails we keep the anchor text up to 120 chars.
        """
        raw = clean_text(anchor.get_text(" ", strip=True))
        if not raw:
            title_attr = (anchor.get("title") or "").strip()
            if title_attr:
                raw = clean_text(title_attr)
        if not raw:
            return ""

        lowered = raw.lower()
        if any(skip == lowered for skip in _SKIP_TITLE_TOKENS):
            return ""

        # Strip the trailing "Son Başvuru Tarihi : ..." tail that
        # Peoplise appends to most anchor texts.
        for marker in (
            " son başvuru tarihi",
            " son basvuru tarihi",
            "  ",
        ):
            idx = raw.lower().find(marker)
            if idx > 0:
                raw = raw[:idx].strip(" -–|•")
                break

        # Heuristic: peel off a trailing location segment (city, county,
        # remote). This is best-effort; the scorer only needs the title.
        location_markers = (
            " istanbul",
            " ankara",
            " izmir",
            " kocaeli",
            " gebze",
            " bursa",
            " eskişehir",
            " remote",
            " hibrit",
            " hybrid",
        )
        best_split = -1
        for marker in location_markers:
            idx = raw.lower().rfind(marker)
            if idx > best_split:
                best_split = idx
        if best_split > 8:
            raw = raw[:best_split].strip(" -–|•,")

        if len(raw) < 3:
            return ""
        return raw[:160]


__all__ = ["PeopliseSource"]