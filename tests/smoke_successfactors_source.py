"""Smoke tests for SuccessFactorsSource.

Run with ``python tests/smoke_successfactors_source.py`` from the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sources.successfactors_source import SuccessFactorsSource  # noqa: E402

failures: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"{name}_OK {detail}".rstrip())
    else:
        print(f"{name}_FAIL {detail}")
        failures.append(name)


def test_rmk_table_jobs() -> None:
    html = """
    <html><body>
      <table>
        <tr>
          <td><a href="/job/Istanbul-Junior-Software-Developer-TR-34000/123456/">Junior Software Developer</a></td>
          <td>Istanbul, TR</td>
        </tr>
        <tr>
          <td><a href="/job/Ankara-Application-Support-Specialist-TR-06000/654321/">Application Support Specialist</a></td>
          <td>Ankara, TR</td>
        </tr>
      </table>
    </body></html>
    """
    source = SuccessFactorsSource("SAP Türkiye", "https://jobs.sap.com/go/T%C3%BCrkiye/9054501/")
    jobs = source._parse_jobs(html)
    _record("SUCCESSFACTORS_RMK_COUNT", len(jobs) == 2, f"count={len(jobs)}")
    _record("SUCCESSFACTORS_RMK_TITLE", jobs[0].title == "Junior Software Developer", jobs[0].title if jobs else "")
    _record("SUCCESSFACTORS_RMK_LOCATION", jobs[0].location == "Istanbul, TR", str(jobs[0].location if jobs else None))
    _record("SUCCESSFACTORS_RMK_URL", jobs[0].url.startswith("https://jobs.sap.com/job/"), jobs[0].url if jobs else "")


def test_career_ns_job_listing() -> None:
    html = """
    <html><body>
      <a href="/career?career_ns=job_listing&company=foo&career_job_req_id=123&rcm_site_locale=tr_TR">
        Java Backend Developer
      </a>
      <a href="/career?company=foo&navBarLevel=SEARCH">Search Jobs</a>
    </body></html>
    """
    source = SuccessFactorsSource("Foo Corp", "https://career5.successfactors.eu/career?company=foo")
    jobs = source._parse_jobs(html)
    _record("SUCCESSFACTORS_CAREER_COUNT", len(jobs) == 1, f"count={len(jobs)}")
    _record("SUCCESSFACTORS_CAREER_TITLE", jobs[0].title == "Java Backend Developer", jobs[0].title if jobs else "")


def test_empty_marker() -> None:
    source = SuccessFactorsSource("Empty Corp", "https://example.com/careers")
    jobs = source._parse_jobs("<html><body>No jobs found</body></html>")
    _record("SUCCESSFACTORS_EMPTY", jobs == [], f"count={len(jobs)}")


if __name__ == "__main__":
    test_rmk_table_jobs()
    test_career_ns_job_listing()
    test_empty_marker()
    if failures:
        print("FAILURES", failures)
        raise SystemExit(1)
    print("SUCCESSFACTORS_SOURCE_SMOKE_OK")
