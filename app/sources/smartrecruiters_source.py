"""SmartRecruiters public career-page / postings source."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from app.sources.ats_helpers import fetch_with_retry, DEFAULT_HEADERS, JSON_HEADERS, REQUEST_TIMEOUT, absolute_url, clean_text, html_to_text, make_job, source_slug, utc_now_iso
from app.sources.base_source import BaseSource
from app.models.job import Job
from app.utils.logger import logger


class SmartRecruitersSource(BaseSource):
    """Fetch public postings from SmartRecruiters.

    The source first tries the public postings API shape used by many
    SmartRecruiters boards, then falls back to parsing the public career page.
    """

    def __init__(
        self,
        company_name: str,
        company_slug: str,
        careers_url: str | None = None,
        source_name: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        if not company_name:
            raise ValueError("SmartRecruitersSource requires company_name")
        if not company_slug:
            raise ValueError("SmartRecruitersSource requires company_slug")
        self.company_name = company_name
        self.company_slug = company_slug.strip().strip("/")
        self.careers_url = careers_url or f"https://careers.smartrecruiters.com/{self.company_slug}"
        self.timeout = timeout
        self.name = source_name or source_slug("smartrecruiters", company_name)
        self.api_url = f"https://api.smartrecruiters.com/v1/companies/{self.company_slug}/postings?limit=100"

    def fetch_jobs(self) -> list[Job]:
        try:
            response = fetch_with_retry(self.api_url, timeout=self.timeout, headers=JSON_HEADERS)
            if response.status_code < 400:
                jobs = self._parse_api(response.json())
                if jobs:
                    return jobs
        except Exception as exc:  # noqa: BLE001 - fallback to HTML page
            logger.debug("SmartRecruiters API fallback for '{}': {}", self.name, exc)

        response = fetch_with_retry(self.careers_url, timeout=self.timeout, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return self._parse_html(response.text)

    def _parse_api(self, data: object) -> list[Job]:
        if not isinstance(data, dict):
            return []
        raw_jobs = data.get("content") or data.get("postings") or []
        if not isinstance(raw_jobs, list):
            return []
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            title = item.get("name") or item.get("title") or ""
            ref = item.get("ref") or item.get("id") or ""
            url = item.get("url") or item.get("applyUrl") or ""
            if not url and ref:
                url = f"https://jobs.smartrecruiters.com/{self.company_slug}/{ref}"
            if not url or url in seen:
                continue
            location_obj = item.get("location") or {}
            location = None
            if isinstance(location_obj, dict):
                location = location_obj.get("fullLocation") or location_obj.get("city") or location_obj.get("country")
            department = item.get("department") or {}
            department_name = department.get("label") if isinstance(department, dict) else None
            job = make_job(
                title=title,
                company=self.company_name,
                location=location,
                seniority=department_name,
                source=self.name,
                url=url,
                description=html_to_text(item.get("description")),
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)
        logger.info("SmartRecruiters source '{}' parsed {} job(s) via API.", self.name, len(jobs))
        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href") or "")
            lowered = href.lower()
            if "jobs.smartrecruiters.com" not in lowered and "/job/" not in lowered:
                continue
            if "search" in lowered or "login" in lowered:
                continue
            url = absolute_url(self.careers_url, href)
            if url in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if len(title) < 3 or title.lower() in {"find out more", "apply", "learn more"}:
                continue
            context = anchor.find_parent(["li", "article", "div"]) or anchor
            description = clean_text(context.get_text(" ", strip=True)) if isinstance(context, Tag) else title
            job = make_job(
                title=title,
                company=self.company_name,
                location=None,
                source=self.name,
                url=url,
                description=description,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)
        logger.info("SmartRecruiters source '{}' parsed {} job(s) via HTML.", self.name, len(jobs))
        return jobs


__all__ = ["SmartRecruitersSource"]
