"""Workable public career-page source."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.sources.ats_helpers import fetch_with_retry, DEFAULT_HEADERS, REQUEST_TIMEOUT, absolute_url, clean_text, html_to_text, make_job, source_slug, utc_now_iso
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger

_JOB_PATH_RE = re.compile(r"/j/[A-Z0-9]{6,}", re.IGNORECASE)


class WorkableSource(BaseSource):
    """Fetch jobs from a public ``apply.workable.com/<account>`` board.

    Workable's authenticated SPI API is not used here. This source reads the
    public board HTML and extracts job-detail anchors under ``/j/<shortcode>``.
    """

    def __init__(
        self,
        company_name: str,
        account: str | None = None,
        careers_url: str | None = None,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("WorkableSource requires company_name")
        if not account and not careers_url:
            raise ValueError("WorkableSource requires account or careers_url")
        self.company_name = company_name
        self.account = (account or self._account_from_url(careers_url or "")).strip().strip("/")
        self.careers_url = careers_url or f"https://apply.workable.com/{self.account}/"
        self.timeout = timeout
        self.name = source_name or source_slug("workable", company_name)

    @staticmethod
    def _account_from_url(url: str) -> str:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        return parts[0] if parts else ""

    def fetch_jobs(self) -> list[Job]:
        response = fetch_with_retry(self.careers_url, timeout=self.timeout, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return self._parse_jobs(response.text)

    def _parse_jobs(self, html: str) -> list[Job]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        # 1) Normal public anchors. This is the most stable surface for the board.
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "")
            if not _JOB_PATH_RE.search(href):
                continue
            url = absolute_url(self.careers_url, href)
            if url in seen:
                continue
            title = self._extract_title(anchor)
            if not title:
                continue
            context = anchor.find_parent(["li", "article", "div"]) or anchor
            description = clean_text(context.get_text(" ", strip=True)) if isinstance(context, Tag) else title
            location = self._extract_location(description)
            job = make_job(
                title=title,
                company=self.company_name,
                location=location,
                source=self.name,
                url=url,
                description=description,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)

        # 2) Some Workable boards expose serialized job URLs in inline JSON.
        if not jobs:
            for shortcode in sorted(set(_JOB_PATH_RE.findall(html))):
                url = absolute_url(self.careers_url, shortcode)
                if url in seen:
                    continue
                title = self._title_near_shortcode(html, shortcode)
                job = make_job(
                    title=title or f"Workable Job {shortcode.rsplit('/', 1)[-1]}",
                    company=self.company_name,
                    location=None,
                    source=self.name,
                    url=url,
                    description=None,
                    discovered_at=discovered_at,
                )
                if job:
                    seen.add(url)
                    jobs.append(job)

        logger.info("Workable source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs

    def _extract_title(self, anchor: Tag) -> str:
        candidates = [
            anchor.get_text(" ", strip=True),
            str(anchor.get("title") or ""),
            str(anchor.get("aria-label") or ""),
        ]
        for candidate in candidates:
            title = clean_text(candidate)
            title = re.sub(r"\bapply\b", "", title, flags=re.IGNORECASE).strip(" -–|•")
            if len(title) >= 3 and title.lower() not in {"apply", "view job", "learn more"}:
                return title
        return ""

    @staticmethod
    def _extract_location(text: str) -> str | None:
        # Lightweight heuristic; detailed locations usually appear in the card text.
        for sep in (" · ", " | ", " - "):
            if sep in text:
                parts = [clean_text(part) for part in text.split(sep) if clean_text(part)]
                for part in parts[1:]:
                    if any(token in part.lower() for token in ("turkey", "türkiye", "istanbul", "ankara", "izmir", "remote", "hybrid")):
                        return part
        return None

    @staticmethod
    def _title_near_shortcode(html: str, shortcode: str) -> str:
        idx = html.find(shortcode)
        if idx < 0:
            return ""
        window = html[max(0, idx - 500): idx + 500]
        # Try to recover a JSON title close to the URL without parsing arbitrary JS.
        for pattern in (r'"title"\s*:\s*"([^"]+)"', r'"jobTitle"\s*:\s*"([^"]+)"'):
            match = re.search(pattern, window)
            if match:
                try:
                    return clean_text(json.loads(f'"{match.group(1)}"'))
                except Exception:
                    return clean_text(match.group(1))
        return ""


__all__ = ["WorkableSource"]
