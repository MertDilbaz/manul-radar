"""Python.org jobs RSS feed source.

Python.org publishes its job board as a free RSS feed at:::

    https://www.python.org/jobs/feed/

The feed is standard RSS 2.0: each ``<item>`` contains ``<title>``,
``<link>``, ``<description>`` (HTML), ``<pubDate>`` and a handful of
custom tags (``<job-type>``, ``<category>``, ``<dc:creator>``, etc.).
Because the feed is Python-focused it skews toward entry-level /
junior developer roles that explicitly mention Python, which makes it
a useful complement to the broader API-based sources.

Parsing uses the stdlib :mod:`xml.etree.ElementTree`, which is always
available (unlike the BeautifulSoup ``"xml"`` feature, which requires
the optional ``lxml`` dependency that is not in ``requirements.txt``).
``dc:creator`` and other namespaced tags are matched defensively with
a local-name lookup so missing namespaces never break a feed.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.models.job import Job
from app.sources.ats_helpers import (
    DEFAULT_HEADERS,
    REQUEST_TIMEOUT,
    clean_text,
    fetch_with_retry,
    html_to_text,
    make_job,
    utc_now_iso,
)
from app.sources.base_source import BaseSource
from app.utils.logger import logger


_FEED_URL = "https://www.python.org/jobs/feed/"


class PythonJobsSource(BaseSource):
    """Fetch jobs from the Python.org RSS feed.

    Args:
        source_name: Name stamped on every emitted ``Job.source``.
            Defaults to ``"python_jobs"``.
        timeout: Per-request timeout in seconds.
    """

    name: str = "python_jobs"
    timeout: int = REQUEST_TIMEOUT
    feed_url: str = _FEED_URL

    def __init__(
        self,
        source_name: str = "python_jobs",
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        self.name = source_name or "python_jobs"
        self.timeout = int(timeout)
        self.feed_url = _FEED_URL

    def fetch_jobs(self) -> list[Job]:
        """GET the Python.org RSS feed and return normalized ``Job`` instances.

        On a network or parse error the source logs the failure and
        returns ``[]`` rather than propagating the exception.
        """
        try:
            response = fetch_with_retry(
                self.feed_url,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — surface as zero results
            logger.warning(
                "Python.org Jobs source '{}' could not fetch {}: {}",
                self.name,
                self.feed_url,
                exc,
            )
            return []

        jobs = self._parse_feed(response.text)
        logger.info("Python.org Jobs source '{}' parsed {} job(s).", self.name, len(jobs))
        return jobs

    # ---------------------- pure parsing (no I/O) ----------------------

    def _parse_feed(self, xml_text: str) -> list[Job]:
        """Parse the RSS XML body into ``Job`` instances."""
        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning(
                "Python.org Jobs source '{}' could not parse feed XML: {}",
                self.name,
                exc,
            )
            return []

        discovered_at = utc_now_iso()
        jobs: list[Job] = []
        seen: set[str] = set()

        for item in root.iter("item"):
            title = _child_text(item, "title")
            url = _child_text(item, "link")
            if not url or url in seen:
                continue
            company = (
                _child_text(item, "creator")
                or _child_text(item, "author")
                or ""
            )
            description = html_to_text(_child_text(item, "description"))
            published_at = _child_text(item, "pubDate") or None
            work_type = _child_text(item, "job-type") or None

            job = make_job(
                title=title,
                company=company or "Python.org",
                location=None,
                source=self.name,
                url=url,
                description=description,
                work_type=work_type or None,
                published_at=published_at,
                discovered_at=discovered_at,
            )
            if job:
                seen.add(url)
                jobs.append(job)

        return jobs


def _child_text(parent: ET.Element, local_name: str) -> str:
    """Return stripped text of the first child matching a local tag name.

    RSS feeds mix plain tags (``title``, ``link``) with namespaced ones
    (``dc:creator``). ElementTree expands namespaces into Clark notation
    (``{http://purl.org/dc/elements/1.1/}creator``), so we match on the
    local part only — ``local_name == "creator"`` matches both a bare
    ``<creator>`` and a ``<dc:creator>``. Returns ``""`` when absent.
    """
    if parent is None:
        return ""
    for child in list(parent):
        tag = child.tag or ""
        # Clark notation: "{ns}local" -> compare local part; else plain tag.
        local = tag.rsplit("}", 1)[-1] if "}" in tag else tag
        if local == local_name:
            return clean_text(child.text or "")
    return ""


__all__ = ["PythonJobsSource"]
