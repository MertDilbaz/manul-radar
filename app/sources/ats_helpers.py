"""Shared helpers for public ATS job sources."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.models.job import Job
from app.utils.logger import logger

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

# Retry configuration for transient network failures. The monitor runs
# once per weekday in CI, so a single flaky response means lost jobs for
# that day. Three attempts with exponential backoff (1s, 2s) catches
# the common case (slow DNS, temporary 502/503, rate-limit 429) without
# adding meaningful latency to the happy path.
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0

# HTTP status codes that justify a retry. 429 (Too Many Requests) is
# the most common; 502/503/504 are gateway errors that are often
# transient. We deliberately do NOT retry on 4xx client errors other
# than 429 (a 403 or 404 is a configuration / permissions issue, not a
# transient failure).
_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_with_retry(
    url: str,
    *,
    headers: dict | None = None,
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_RETRY_BACKOFF,
) -> requests.Response:
    """GET ``url`` with retry and exponential backoff for transient failures.

    Retries on:

    * ``requests.exceptions.ConnectionError`` / ``Timeout`` — network
      blips, DNS hiccup, slow server.
    * HTTP 429 / 502 / 503 / 504 — the server is temporarily
      unavailable or rate-limiting us.

    Non-retryable errors (``HTTPError`` from a 4xx other than 429,
    ``JSONDecodeError``, etc.) propagate immediately after the first
    attempt so the caller can handle them.

    The backoff is exponential: ``backoff``, ``backoff * 2``,
    ``backoff * 4`` … capped at 10 seconds per sleep.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                headers=headers or DEFAULT_HEADERS,
                timeout=timeout,
            )
            if response.status_code in _RETRY_STATUS_CODES and attempt < max_retries:
                sleep_for = min(backoff * (2 ** (attempt - 1)), 10)
                logger.warning(
                    f"fetch_with_retry: {url} returned HTTP {response.status_code}; "
                    f"retry {attempt}/{max_retries} after {sleep_for:.1f}s."
                )
                time.sleep(sleep_for)
                continue
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                sleep_for = min(backoff * (2 ** (attempt - 1)), 10)
                logger.warning(
                    f"fetch_with_retry: {url} raised {type(exc).__name__}; "
                    f"retry {attempt}/{max_retries} after {sleep_for:.1f}s."
                )
                time.sleep(sleep_for)
            else:
                raise

    # Should not reach here, but just in case:
    if last_exc:
        raise last_exc
    raise RuntimeError("fetch_with_retry exhausted retries without a response")


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


__all__ = [
    "REQUEST_TIMEOUT",
    "DEFAULT_HEADERS",
    "JSON_HEADERS",
    "utc_now_iso",
    "fetch_with_retry",
    "clean_text",
    "html_to_text",
    "source_slug",
    "make_job",
    "absolute_url",
]
