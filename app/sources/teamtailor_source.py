"""Teamtailor public career-page source."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.sources.ats_helpers import DEFAULT_HEADERS, REQUEST_TIMEOUT, absolute_url, clean_text, make_job, source_slug, utc_now_iso
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger

_JOB_PATH_RE = re.compile(r"/jobs/\d+", re.IGNORECASE)


class TeamtailorSource(BaseSource):
    """Fetch visible job cards from a public Teamtailor career site."""

    def __init__(
        self,
        company_name: str,
        careers_url: str,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("TeamtailorSource requires company_name")
        if not careers_url:
            raise ValueError("TeamtailorSource requires careers_url")
        self.company_name = company_name
        self.careers_url = careers_url.rstrip("/") + "/"
        self.timeout = timeout
        self.name = source_name or source_slug("teamtailor", company_name)

    def fetch_jobs(self) -> list[Job]:
        response = requests.get(self.careers_url, timeout=self.timeout, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return self._parse_jobs(response.text)

    def _parse_jobs(self, html: str) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "")
            if not _JOB_PATH_RE.search(href):
                continue
            url = absolute_url(self.careers_url, href)
            if url in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title or title.lower() in {"apply", "read more", "learn more"}:
                parent = anchor.find_parent(["li", "article", "div"])
                title = self._title_from_context(parent) if isinstance(parent, Tag) else ""
            if not title:
                continue
            context = anchor.find_parent(["li", "article", "div"]) or anchor
            description = clean_text(context.get_text(" ", strip=True)) if isinstance(context, Tag) else title
            job = make_job(
                title=title,
                company=self.company_name,
                location=self._location_hint(description),
                source=self.name,
                url=url,
                description=description,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)
        logger.info("Teamtailor source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs

    @staticmethod
    def _title_from_context(context: Tag | None) -> str:
        if context is None:
            return ""
        for tag_name in ("h1", "h2", "h3", "h4"):
            node = context.find(tag_name)
            if isinstance(node, Tag):
                title = clean_text(node.get_text(" ", strip=True))
                if title:
                    return title
        return ""

    @staticmethod
    def _location_hint(text: str) -> str | None:
        lowered = text.lower()
        for token in ("istanbul", "ankara", "izmir", "turkey", "türkiye", "remote", "hybrid"):
            if token in lowered:
                return token
        return None


__all__ = ["TeamtailorSource"]
