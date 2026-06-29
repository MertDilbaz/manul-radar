# app/sources/successfactors_source.py

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger

from app.models.job import Job
from app.sources.ats_helpers import fetch_with_retry, source_slug
from app.sources.base_source import BaseSource


class SuccessFactorsSource(BaseSource):
    def __init__(
        self,
        company_name: str,
        careers_url: str,
        source_name: str | None = None,
        timeout: int = 20,
    ) -> None:
        """Configure the SuccessFactors source.

        ``company_name`` and ``careers_url`` are required; ``source_name``
        is optional. When ``source_name`` is supplied, it overrides the
        derived slug (``successfactors_<company_slug>``) so a config
        can pin the ``name`` attribute to a stable id even if the
        company field is later renamed. ``timeout`` is the network
        timeout in seconds.
        """
        self.company_name = company_name
        self.careers_url = careers_url
        self.timeout = timeout
        self.name = source_name or source_slug("successfactors", company_name)

    def fetch_jobs(self) -> list[Job]:
        response = fetch_with_retry(
            self.careers_url,
            timeout=self.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    def _parse_jobs(self, html: str) -> list[Job]:
        """Parse HTML into ``Job`` instances; ``[]`` on failure."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        discovered_at = datetime.now(timezone.utc).isoformat()

        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(" ", strip=True)

            if not self._looks_like_job_link(href, text):
                continue

            url = urljoin(self.careers_url, href)

            if url in seen_urls:
                continue

            seen_urls.add(url)

            title = self._clean_title(text)
            if not title:
                continue

            # Try to extract a location from the anchor's parent row/cell
            location = self._extract_location(link)

            jobs.append(
                Job(
                    title=title,
                    company=self.company_name,
                    location=location,
                    work_type=None,
                    seniority=None,
                    source=self.name,
                    url=url,
                    description=None,
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

    @staticmethod
    def _extract_location(link) -> str | None:
        """Best-effort location extraction from a table row sibling.

        SuccessFactors job listings are typically inside ``<tr>`` rows
        where the first ``<td>`` holds the job link and a sibling ``<td>``
        holds the location text.
        """
        parent = link.parent
        if parent is None:
            return None
        # Look at sibling cells in the same row
        for sibling in parent.find_all_next(["td", "div"]):
            text = sibling.get_text(strip=True)
            if text and text != link.get_text(strip=True):
                # Looks like a city/region reference
                if len(text) < 100 and ("," in text or any(
                    city in text.lower()
                    for city in ("istanbul", "ankara", "izmir", "tr", "turkey", "türkiye")
                )):
                    return text
        return None

    def _looks_like_job_link(self, href: str, text: str) -> bool:
        href_lower = href.lower()
        text_lower = text.lower()

        if not text or len(text.strip()) < 3:
            return False

        job_url_signals = [
            "job",
            "career",
            "rcmjobdetail",
            "jobreqid",
            "jobid",
        ]

        job_text_signals = [
            "developer",
            "engineer",
            "specialist",
            "analyst",
            "consultant",
            "uzman",
            "mühendis",
            "yazılım",
            "destek",
            "java",
            "backend",
            "erp",
            "sql",
        ]

        return any(signal in href_lower for signal in job_url_signals) and any(
            signal in text_lower for signal in job_text_signals
        )

    def _clean_title(self, text: str) -> str:
        title = " ".join(text.split())

        ignored_titles = {
            "search jobs",
            "view job",
            "apply now",
            "başvur",
            "işe başvur",
            "kariyer",
            "careers",
        }

        if title.lower() in ignored_titles:
            return ""

        return title