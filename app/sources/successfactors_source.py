"""Generic SAP SuccessFactors / RMK career-page source.

Many corporate career sites powered by SAP SuccessFactors expose a
public listing page in one of these shapes:

* branded RMK pages such as ``https://jobs.sap.com/go/T%C3%BCrkiye/9054501/``;
* ``career*.successfactors.*`` pages whose job-detail URLs contain
  ``career_ns=job_listing`` / ``career_job_req_id``;
* company-branded pages that still render normal job-detail anchors
  under ``/job/...`` paths.

This source intentionally starts conservative: it reads the public HTML
listing page and extracts visible job-detail anchors. It does not try to
bypass captcha, login, geo restrictions, JavaScript-only APIs, or closed
endpoints. If a specific company renders jobs only through a private JSON
endpoint, add a company-specific adapter later rather than making this
parser evasive.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.models.job import Job
from app.sources.base_source import BaseSource
from app.utils.logger import logger

REQUEST_TIMEOUT: int = 20

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_EMPTY_MARKERS: tuple[str, ...] = (
    "no jobs found",
    "no results found",
    "no open positions",
    "açık pozisyon bulunamadı",
    "acik pozisyon bulunamadi",
)

# URL tokens that are common on SuccessFactors/RMK job-detail anchors.
_JOB_HREF_TOKENS: tuple[str, ...] = (
    "/job/",
    "career_ns=job_listing",
    "career_job_req_id=",
    "jobreqid=",
    "job_id=",
    "jobid=",
    "job_id",
    "job requisition",
)

# Listing / chrome / account links to skip even if they contain "job".
_SKIP_HREF_TOKENS: tuple[str, ...] = (
    "createalert",
    "create-alert",
    "talentcommunity",
    "talent-community",
    "jobalert",
    "job-alert",
    "login",
    "profile",
    "search",
    "mapsearch",
    "jobcart",
    "savedjobs",
    "privacy",
    "cookie",
    "terms",
    "select language",
)

_SKIP_TITLES: tuple[str, ...] = (
    "apply now",
    "başvur",
    "basvur",
    "view job",
    "view profile",
    "search jobs",
    "search results",
    "create alert",
    "job alert",
    "share this job",
    "previous",
    "next",
    "page 1",
    "page 2",
    "page 3",
)

_LOCATION_LABELS: tuple[str, ...] = (
    "city",
    "location",
    "lokasyon",
    "şehir",
    "sehir",
)


class SuccessFactorsSource(BaseSource):
    """Fetch job listings from a public SuccessFactors/RMK listing page."""

    name: str = ""
    company_name: str = ""
    careers_url: str = ""

    def __init__(
        self,
        company_name: str,
        careers_url: str,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("SuccessFactorsSource requires company_name")
        if not careers_url:
            raise ValueError("SuccessFactorsSource requires careers_url")
        self.company_name = company_name
        self.careers_url = careers_url
        self.timeout = timeout
        self.name = source_name or self._derive_source_name(company_name)

    @staticmethod
    def _derive_source_name(company_name: str) -> str:
        normalized = company_name.lower()
        replacements = str.maketrans(
            {
                "ı": "i",
                "ğ": "g",
                "ü": "u",
                "ş": "s",
                "ö": "o",
                "ç": "c",
            }
        )
        normalized = normalized.translate(replacements)
        chars: list[str] = []
        prev_underscore = False
        for char in normalized:
            if char.isalnum():
                chars.append(char)
                prev_underscore = False
            elif not prev_underscore:
                chars.append("_")
                prev_underscore = True
        return "successfactors_" + "".join(chars).strip("_")

    def fetch_jobs(self) -> list[Job]:
        response = requests.get(
            self.careers_url,
            timeout=self.timeout,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    def _parse_jobs(self, html: str) -> list[Job]:
        if not html:
            return []

        lowered = html.lower()
        for marker in _EMPTY_MARKERS:
            if marker in lowered:
                logger.debug(
                    f"SuccessFactors page ({self.company_name}) appears empty via {marker!r}."
                )
                return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001 - defensive parser boundary
            logger.warning(
                f"SuccessFactors HTML for {self.company_name} could not be parsed: {exc}"
            )
            return []

        seen_urls: set[str] = set()
        jobs: list[Job] = []
        discovered_at = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "").strip()
            if not self._looks_like_job_href(href):
                continue

            url = self._canonical_url(href)
            if not url or url in seen_urls:
                continue

            title = self._extract_title(anchor)
            if not title:
                continue

            location = self._extract_location(anchor)
            description = self._extract_context_text(anchor)

            seen_urls.add(url)
            jobs.append(
                Job(
                    title=title,
                    company=self.company_name,
                    location=location,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=url,
                    description=description,
                    published_at=None,
                    discovered_at=discovered_at,
                )
            )

        logger.info(
            "SuccessFactors source '{}' parsed {} job(s).",
            self.name,
            len(jobs),
        )
        return jobs

    def _looks_like_job_href(self, href: str) -> bool:
        if not href:
            return False
        lowered = href.lower()
        if any(token in lowered for token in _SKIP_HREF_TOKENS):
            return False
        if any(token in lowered for token in _JOB_HREF_TOKENS):
            return True

        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if any(key.lower() in {"career_job_req_id", "jobreqid", "jobid", "job_id"} for key in query):
            return True
        return False

    def _canonical_url(self, href: str) -> str:
        return urljoin(self.careers_url, href)

    def _extract_title(self, anchor: Tag) -> str:
        candidates = [
            anchor.get_text(" ", strip=True),
            str(anchor.get("title") or "").strip(),
            str(anchor.get("aria-label") or "").strip(),
        ]
        for candidate in candidates:
            cleaned = self._clean_text(candidate)
            if self._is_valid_title(cleaned):
                return cleaned
        return ""

    def _is_valid_title(self, title: str) -> bool:
        if len(title) < 3:
            return False
        lowered = title.lower()
        if lowered in _SKIP_TITLES:
            return False
        if any(skip == lowered for skip in _SKIP_TITLES):
            return False
        # Very long anchor blobs are usually table/list context, not title.
        if len(title) > 180:
            return False
        return True

    def _extract_location(self, anchor: Tag) -> str | None:
        row = anchor.find_parent("tr")
        if row is not None:
            cells = [self._clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            cells = [cell for cell in cells if cell]
            if len(cells) >= 2:
                # RMK tables often render as Title | City. Keep the first non-title cell.
                title = self._clean_text(anchor.get_text(" ", strip=True))
                for cell in cells[1:]:
                    if cell and cell != title and len(cell) <= 80:
                        return cell

        card = anchor.find_parent(["li", "article", "div"])
        if card is not None:
            for attr in ("class", "data-automation-id", "data-testid"):
                for node in card.find_all(attrs={attr: True}):
                    attr_value = " ".join(node.get(attr) if isinstance(node.get(attr), list) else [str(node.get(attr))])
                    if any(label in attr_value.lower() for label in _LOCATION_LABELS):
                        text = self._clean_text(node.get_text(" ", strip=True))
                        if text and len(text) <= 100:
                            return text
        return None

    def _extract_context_text(self, anchor: Tag) -> str | None:
        parent = anchor.find_parent(["tr", "li", "article", "div"])
        if parent is None:
            return None
        text = self._clean_text(parent.get_text(" ", strip=True))
        if not text:
            return None
        # Keep context useful for scoring but bounded for DB/Telegram.
        return text[:1000]

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join((text or "").split())


__all__ = ["SuccessFactorsSource"]
