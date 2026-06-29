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


def test_peoplise() -> None:
    from app.sources.peoplise_source import PeopliseSource

    html = (
        '<html><body>'
        '<a href="javascript:void(0);">noise</a>'
        '<a href="/logo/application/landing/abc-123">Junior Java Developer Istanbul</a>'
        '<a href="/logo/application/landing/def-456">Backend Engineer, Spring Boot</a>'
        '<a href="/other/application/landing/zzz-999">wrong account, must be skipped</a>'
        '<a href="https://twitter.com/logoyazilim">social, must be skipped</a>'
        '</body></html>'
    )
    source = PeopliseSource("Logo Yazılım", "https://live.peoplise.com/logo/career")
    jobs = source._parse_jobs(html)
    _record("PEOPLISE_COUNT", len(jobs) == 2, f"count={len(jobs)}")
    _record("PEOPLISE_URL", jobs and all("/application/landing/" in j.url for j in jobs),
            ", ".join(j.url for j in jobs))
    _record("PEOPLISE_TITLE", jobs and "Junior Java" in jobs[0].title,
            jobs[0].title if jobs else "")


def test_hirex() -> None:
    from app.sources.hirex_source import HirexSource

    # Empty page is a normal result, not an error.
    source = HirexSource("Papara", "https://app.gethirex.com/o/papara/")
    _record("HIREX_EMPTY_OK", source._parse_jobs("") == [], "empty html -> []")
    _record("HIREX_NO_JOBS_OK", source._parse_jobs("<html><body>no open positions here</body></html>") == [],
            "no-open-positions marker -> []")

    html = (
        '<html><body>'
        '<a href="/o/papara/">listing self-link</a>'
        '<a href="/o/papara/senior-backend-engineer">Senior Backend Engineer</a>'
        '</body></html>'
    )
    jobs = source._parse_jobs(html)
    _record("HIREX_ANCHOR_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("HIREX_ANCHOR_TITLE", jobs and "Senior Backend" in jobs[0].title,
            jobs[0].title if jobs else "")


def test_zoho_recruit() -> None:
    from app.sources.zoho_recruit_source import ZohoRecruitSource

    source = ZohoRecruitSource(
        "Param",
        "https://param.zohorecruit.com/jobs/PARAM-Kariyer",
    )

    # Empty / no-results pages are normal.
    _record("ZOHO_EMPTY_OK", source._parse_jobs("") == [], "empty html -> []")
    _record(
        "ZOHO_NO_JOBS_OK",
        source._parse_jobs("<html>no open positions</html>") == [],
        "no-open-positions marker -> []",
    )

    # Anchor with /jobs/<portal>/<slug> shape.
    html_anchor = (
        '<html><body>'
        '<a href="/jobs/PARAM-Kariyer/junior-java-developer">'
        'Junior Java Developer</a>'
        '<a href="https://param.zohorecruit.com/jobs/PARAM-Kariyer">'
        'listing self-link</a>'
        '</body></html>'
    )
    jobs = source._parse_jobs(html_anchor)
    _record("ZOHO_ANCHOR_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record(
        "ZOHO_ANCHOR_URL",
        jobs and "junior-java-developer" in jobs[0].url,
        jobs[0].url if jobs else "",
    )

    # Anchor with ViewJobPosting?jobId=... shape.
    html_viewjob = (
        '<html><body>'
        '<a href="/recruit/v2/ViewJobPosting?jobId=ABC123&amp;src=JB-10061">'
        'Backend Engineer</a>'
        '</body></html>'
    )
    jobs_view = source._parse_jobs(html_viewjob)
    _record("ZOHO_VIEWJOB_COUNT", len(jobs_view) == 1, f"count={len(jobs_view)}")
    _record(
        "ZOHO_VIEWJOB_URL",
        jobs_view and jobs_view[0].url == "https://param.zohorecruit.com/recruit/v2/ViewJobPosting?jobId=ABC123",
        jobs_view[0].url if jobs_view else "",
    )


if __name__ == "__main__":
    test_greenhouse()
    test_lever()
    test_workable()
    test_smartrecruiters()
    test_teamtailor()
    test_peoplise()
    test_hirex()
    test_zoho_recruit()
    if failures:
        print("FAILURES", failures)
        raise SystemExit(1)
    print("ATS_SOURCES_SMOKE_OK")
