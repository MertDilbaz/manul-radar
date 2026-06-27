"""Shared helpers for public ATS job sources."""
from __future__ import annotations

import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models.job import Job

REQUEST_TIMEOUT = 20
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}
JSON_HEADERS = {
    **DEFAULT_HEADERS,
    "Accept": "application/json,text/plain,*/*",
}


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def clean_text(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def html_to_text(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return clean_text(text)


def source_slug(prefix: str, value: str) -> str:
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
    normalized = value.lower().translate(replacements)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return f"{prefix}_{normalized}" if normalized else prefix


def make_job(
    *,
    title: str,
    company: str,
    location: str | None,
    source: str,
    url: str,
    description: str | None = None,
    work_type: str | None = None,
    seniority: str | None = None,
    published_at: str | None = None,
    discovered_at: str | None = None,
) -> Job | None:
    title = clean_text(title)
    url = clean_text(url)
    if not title or not url:
        return None
    return Job(
        title=title,
        company=clean_text(company),
        location=clean_text(location) or None,
        work_type=clean_text(work_type) or None,
        seniority=clean_text(seniority) or None,
        source=source,
        url=url,
        description=clean_text(description) or None,
        published_at=clean_text(published_at) or None,
        discovered_at=discovered_at or utc_now_iso(),
    )


def absolute_url(base_url: str, href: str) -> str:
    return clean_text(urljoin(base_url, href))
