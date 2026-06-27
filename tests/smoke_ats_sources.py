"""Smoke tests for public ATS source parsers.

Run with ``python tests/smoke_ats_sources.py`` from the project root.
All network calls are monkeypatched; this validates parser contracts without
hitting live boards.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

failures: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"{name}_OK {detail}".rstrip())
    else:
        print(f"{name}_FAIL {detail}")
        failures.append(name)


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from requests import HTTPError
            raise HTTPError(f"status={self.status_code}")

    def json(self):
        return self._json_data


def test_greenhouse() -> None:
    from app.sources.greenhouse_source import GreenhouseSource

    data = {"jobs": [{"title": "Junior Java Developer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Istanbul"}, "content": "<p>Java Spring SQL</p>"}]}
    with mock.patch("requests.get", return_value=_FakeResponse(json_data=data)):
        jobs = GreenhouseSource("Acme", "acme").fetch_jobs()
    _record("GREENHOUSE_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("GREENHOUSE_TITLE", jobs and jobs[0].title == "Junior Java Developer", jobs[0].title if jobs else "")


def test_lever() -> None:
    from app.sources.lever_source import LeverSource

    data = [{"text": "Backend Software Engineer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Istanbul", "team": "Engineering", "commitment": "Full-time"}, "lists": [{"text": "Requirements", "content": ["<p>Java backend SQL</p>"]}]}]
    with mock.patch("requests.get", return_value=_FakeResponse(json_data=data)):
        jobs = LeverSource("Acme", "acme").fetch_jobs()
    _record("LEVER_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("LEVER_LOCATION", jobs and jobs[0].location == "Istanbul", str(jobs[0].location if jobs else None))


def test_workable() -> None:
    from app.sources.workable_source import WorkableSource

    html = '<html><body><a href="/acme/j/ABC123DEF/">Junior Backend Developer</a></body></html>'
    source = WorkableSource("Acme", account="acme")
    jobs = source._parse_jobs(html)
    _record("WORKABLE_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("WORKABLE_URL", jobs and "/j/ABC123DEF" in jobs[0].url, jobs[0].url if jobs else "")


def test_smartrecruiters() -> None:
    from app.sources.smartrecruiters_source import SmartRecruitersSource

    data = {"content": [{"name": "Application Support Specialist", "ref": "abc", "location": {"city": "Istanbul"}, "department": {"label": "IT"}, "description": "SQL application support"}]}
    source = SmartRecruitersSource("Acme", "Acme")
    jobs = source._parse_api(data)
    _record("SMARTRECRUITERS_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("SMARTRECRUITERS_TITLE", jobs and jobs[0].title == "Application Support Specialist", jobs[0].title if jobs else "")


def test_teamtailor() -> None:
    from app.sources.teamtailor_source import TeamtailorSource

    html = '<html><body><article><h3>Software Engineer Java</h3><a href="/jobs/123-software-engineer-java">Read more</a><p>Istanbul Hybrid</p></article></body></html>'
    source = TeamtailorSource("Acme", "https://acme.teamtailor.com/jobs")
    jobs = source._parse_jobs(html)
    _record("TEAMTAILOR_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("TEAMTAILOR_TITLE", jobs and jobs[0].title == "Software Engineer Java", jobs[0].title if jobs else "")


if __name__ == "__main__":
    test_greenhouse()
    test_lever()
    test_workable()
    test_smartrecruiters()
    test_teamtailor()
    if failures:
        print("FAILURES", failures)
        raise SystemExit(1)
    print("ATS_SOURCES_SMOKE_OK")
