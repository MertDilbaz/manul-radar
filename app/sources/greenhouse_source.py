"""Greenhouse public Job Board API source."""
from __future__ import annotations


from app.sources.ats_helpers import fetch_with_retry, JSON_HEADERS, REQUEST_TIMEOUT, html_to_text, make_job, source_slug, utc_now_iso
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger


class GreenhouseSource(BaseSource):
    """Fetch published jobs from Greenhouse's public job board API."""

    def __init__(
        self,
        company_name: str,
        board_token: str,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("GreenhouseSource requires company_name")
        if not board_token:
            raise ValueError("GreenhouseSource requires board_token")
        self.company_name = company_name
        self.board_token = board_token.strip().strip("/")
        self.timeout = timeout
        self.name = source_name or source_slug("greenhouse", company_name)
        self.api_url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs?content=true"

    def fetch_jobs(self) -> list[Job]:
        response = fetch_with_retry(self.api_url, timeout=self.timeout, headers=JSON_HEADERS)
        response.raise_for_status()
        data = response.json()
        raw_jobs = data.get("jobs") if isinstance(data, dict) else []
        if not isinstance(raw_jobs, list):
            return []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            url = item.get("absolute_url") or item.get("url") or ""
            if not url or url in seen:
                continue
            location_obj = item.get("location") or {}
            location = location_obj.get("name") if isinstance(location_obj, dict) else None
            description = html_to_text(item.get("content"))
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
        logger.info("Greenhouse source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs


__all__ = ["GreenhouseSource"]
