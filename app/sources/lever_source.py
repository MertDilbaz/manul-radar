"""Lever public postings API source."""
from __future__ import annotations


from app.sources.ats_helpers import fetch_with_retry, JSON_HEADERS, REQUEST_TIMEOUT, clean_text, html_to_text, make_job, source_slug, utc_now_iso
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger


class LeverSource(BaseSource):
    """Fetch public postings from Lever's public postings endpoint."""

    def __init__(
        self,
        company_name: str,
        company_slug: str,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("LeverSource requires company_name")
        if not company_slug:
            raise ValueError("LeverSource requires company_slug")
        self.company_name = company_name
        self.company_slug = company_slug.strip().strip("/")
        self.timeout = timeout
        self.name = source_name or source_slug("lever", company_name)
        self.api_url = f"https://api.lever.co/v0/postings/{self.company_slug}?mode=json"

    def fetch_jobs(self) -> list[Job]:
        response = fetch_with_retry(self.api_url, timeout=self.timeout, headers=JSON_HEADERS)
        response.raise_for_status()
        data = response.json()
        raw_jobs = data if isinstance(data, list) else []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            title = item.get("text") or item.get("title") or ""
            url = item.get("hostedUrl") or item.get("applyUrl") or ""
            if not url or url in seen:
                continue
            categories = item.get("categories") or {}
            location = categories.get("location") if isinstance(categories, dict) else None
            team = categories.get("team") if isinstance(categories, dict) else None
            commitment = categories.get("commitment") if isinstance(categories, dict) else None
            content_parts: list[str] = []
            for section in item.get("lists") or []:
                if not isinstance(section, dict):
                    continue
                content_parts.append(clean_text(section.get("text")))
                for content in section.get("content") or []:
                    content_parts.append(html_to_text(content))
            description = clean_text(" ".join(part for part in content_parts if part))
            job = make_job(
                title=title,
                company=self.company_name,
                location=location,
                work_type=commitment,
                source=self.name,
                url=url,
                description=description,
                seniority=team,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)
        logger.info("Lever source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs


__all__ = ["LeverSource"]
