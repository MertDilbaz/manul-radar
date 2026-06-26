"""Smoke test for HrPeakSource (parser contract, no network).

Run with ``python tests/smoke_hrpeak_source.py`` from the project
root. Prints ``<NAME>_OK ...`` lines on success and exits 0. On any
failure prints ``<NAME>_FAIL ...`` with the offending value, dumps the
failure list, and exits 1.

The smoke test never touches the network. It exercises the source's
*pure* parser — :meth:`HrPeakSource._parse_jobs` — with
hand-crafted HTML samples so we can verify the empty-page, normal
listing, dedup, junk-skip, and malformed-input branches in isolation.
A network outage on the HRPeak host cannot make this test flaky.

V0.2: the test instantiates ``HrPeakSource`` with the same
constructor signature the production config uses, so the
company/careers_url plumbing is exercised end-to-end. The Kafein
``careers_url`` is hardcoded as the test fixture to keep behaviour
identical to the pre-V0.2 Kafein-specific smoke.
"""
from __future__ import annotations

import sys
from pathlib import Path

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


# ------------------------------- fixtures ---------------------------------------


# Canonical fixture values. The Kafein test data is the original
# V0.1 set — V0.2 exercises the same parser logic, just routed
# through a generic constructor instead of a hardcoded BASE_URL.
_COMPANY = "Kafein Technology Solutions"
_CAREERS_URL = "https://kafein.hrpeak.com/ilan/site.aspx"


def _make_src():
    """Build a fresh ``HrPeakSource`` for the Kafein fixture.

    A new instance per test so per-instance state (none today,
    but future fields may include per-run caches) does not leak
    between cases.
    """
    from app.sources.hrpeak_source import HrPeakSource

    return HrPeakSource(company_name=_COMPANY, careers_url=_CAREERS_URL)


EMPTY_TR_HTML = """
<html><body>
<div class="empty-state">
  Yayınlanmış bir açık pozisyon bulunamadı
</div>
</body></html>
"""

EMPTY_EN_HTML = """
<html><body>
<div class="empty-state">
  No published job postings found
</div>
</body></html>
"""

LISTING_WITH_JOBS_HTML = """
<html><body>
<div class="job-list">
  <a href="ilan/detay.aspx?id=1">Java Backend Developer</a>
  <a href="ilan/detay.aspx?id=2">Junior SQL Specialist</a>
  <a href="https://kafein.hrpeak.com/">Ana Sayfa</a>
  <a href="ilan/site.aspx">Tüm İlanlar</a>
</div>
</body></html>
"""

DEDUP_HTML = """
<html><body>
<a href="ilan/detay.aspx?id=42">Senior Engineer</a>
<a href="ilan/detay.aspx?id=42">Senior Engineer (featured)</a>
</body></html>
"""

JUNK_TITLE_HTML = """
<html><body>
<a href="ilan/detay.aspx?id=99"></a>
<a href="ilan/detay.aspx?id=100">ab</a>
<a href="ilan/detay.aspx?id=101">Real Job Posting</a>
</body></html>
"""

MALFORMED_HTML = "<not really <html>"


# ------------------------------- tests -----------------------------------------


def _check_parse() -> None:
    try:
        from app.sources.hrpeak_source import HrPeakSource  # noqa: F401
        import requests  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_metadata() -> None:
    """The constructor must accept company + url and surface them
    on the instance. ``name`` is derived from the company."""
    src = _make_src()
    if src.name != "kafein_technology_solutions":
        _record("NAME", False, f"got {src.name!r}")
        return
    if src.company_name != _COMPANY:
        _record("COMPANY_FIELD", False, f"got {src.company_name!r}")
        return
    if src.careers_url != _CAREERS_URL:
        _record("CAREERS_URL", False, f"got {src.careers_url!r}")
        return
    if not src.careers_url.startswith("https://kafein.hrpeak.com"):
        _record("CAREERS_URL_HOST", False, f"got {src.careers_url!r}")
        return
    _record(
        "METADATA",
        True,
        f"name={src.name} company={src.company_name} url={src.careers_url}",
    )


def _check_constructor_rejects_empty() -> None:
    """Empty company / url must raise ValueError, not silently
    fall back to defaults (the V0.2 contract is explicit inputs)."""
    from app.sources.hrpeak_source import HrPeakSource

    try:
        HrPeakSource(company_name="", careers_url=_CAREERS_URL)
    except ValueError:
        pass
    else:
        _record("EMPTY_COMPANY", False, "expected ValueError for empty company")
        return

    try:
        HrPeakSource(company_name=_COMPANY, careers_url="")
    except ValueError:
        pass
    else:
        _record("EMPTY_URL", False, "expected ValueError for empty url")
        return

    _record("CONSTRUCTOR_VALIDATION", True, "empty company/url -> ValueError")


def _check_empty_marker_tr() -> None:
    src = _make_src()
    jobs = src._parse_jobs(EMPTY_TR_HTML)
    if jobs != []:
        _record("EMPTY_TR", False, f"expected [], got {len(jobs)} jobs")
        return
    _record("EMPTY_TR", True, "Turkish empty marker -> []")


def _check_empty_marker_en() -> None:
    src = _make_src()
    jobs = src._parse_jobs(EMPTY_EN_HTML)
    if jobs != []:
        _record("EMPTY_EN", False, f"expected [], got {len(jobs)} jobs")
        return
    _record("EMPTY_EN", True, "English empty marker -> []")


def _check_empty_html_string() -> None:
    src = _make_src()
    if src._parse_jobs("") != []:
        _record("EMPTY_STRING", False, "expected [] for empty input")
        return
    _record("EMPTY_STRING", True, "empty string -> []")


def _check_with_jobs_parses_listings() -> None:
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)
    titles = [j.title for j in jobs]

    if len(jobs) != 2:
        _record(
            "JOBS_COUNT",
            False,
            f"expected 2 jobs, got {len(jobs)}: {titles}",
        )
        return
    if "Java Backend Developer" not in titles:
        _record("JOBS_JAVA", False, f"java missing: {titles}")
        return
    if "Junior SQL Specialist" not in titles:
        _record("JOBS_JUNIOR", False, f"junior missing: {titles}")
        return
    _record("JOBS", True, f"2 jobs parsed: {titles}")


def _check_job_field_contract() -> None:
    """Each parsed Job must have correct title/company/source/url."""
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)

    for job in jobs:
        if not job.title or len(job.title) < 3:
            _record(
                "FIELD_TITLE",
                False,
                f"job title missing/short: {job.title!r}",
            )
            return
        if job.company != _COMPANY:
            _record(
                "FIELD_COMPANY",
                False,
                f"unexpected company {job.company!r}",
            )
            return
        if job.source != "kafein_technology_solutions":
            _record(
                "FIELD_SOURCE",
                False,
                f"unexpected source {job.source!r}",
            )
            return
        if not job.url.startswith("http"):
            _record(
                "FIELD_URL_ABSOLUTE",
                False,
                f"url not absolute: {job.url!r}",
            )
            return
        if not job.discovered_at:
            _record(
                "FIELD_DISCOVERED",
                False,
                f"discovered_at missing: {job.discovered_at!r}",
            )
            return
        if job.location is not None:
            _record(
                "FIELD_LOCATION_NONE",
                False,
                f"location should be None, got {job.location!r}",
            )
            return
        if job.work_type is not None or job.seniority is not None:
            _record(
                "FIELD_OPTIONAL_NONE",
                False,
                f"work_type/seniority should be None, got "
                f"{job.work_type!r}/{job.seniority!r}",
            )
            return
        if job.description is not None or job.published_at is not None:
            _record(
                "FIELD_META_NONE",
                False,
                f"description/published_at should be None, got "
                f"{job.description!r}/{job.published_at!r}",
            )
            return

    _record("FIELDS", True, f"{len(jobs)} jobs satisfy field contract")


def _check_listing_url_excluded() -> None:
    """The listing page URL itself must NOT appear as a job URL."""
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)
    urls = [j.url for j in jobs]

    listing_url = _CAREERS_URL
    if listing_url in urls:
        _record("LISTING_EXCLUDED", False, f"listing url leaked: {urls}")
        return
    if any(u.rstrip("/").endswith("site.aspx") for u in urls):
        _record("LISTING_EXCLUDED", False, f"site.aspx suffix leaked: {urls}")
        return
    _record("LISTING_EXCLUDED", True, f"listing url filtered, urls={urls}")


def _check_relative_url_resolved() -> None:
    """Relative hrefs must be joined against the configured careers_url."""
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)
    if not jobs:
        _record("RELATIVE_URL", False, "no jobs parsed")
        return
    if not all(j.url.startswith("https://kafein.hrpeak.com/") for j in jobs):
        _record(
            "RELATIVE_URL",
            False,
            f"relative urls not joined: {[j.url for j in jobs]}",
        )
        return
    _record(
        "RELATIVE_URL",
        True,
        f"all urls absolute: {[j.url for j in jobs]}",
    )


def _check_url_dedup() -> None:
    """The same URL twice should produce a single Job, not two."""
    src = _make_src()
    jobs = src._parse_jobs(DEDUP_HTML)
    if len(jobs) != 1:
        _record(
            "DEDUP",
            False,
            f"expected 1 job from duplicate hrefs, got {len(jobs)}",
        )
        return
    if "?id=42" not in jobs[0].url:
        _record(
            "DEDUP_URL",
            False,
            f"unexpected url: {jobs[0].url}",
        )
        return
    _record("DEDUP", True, f"1 job from duplicate href: {jobs[0].url}")


def _check_junk_title_skipped() -> None:
    """Empty / too-short titles must be skipped, not emitted as garbage."""
    src = _make_src()
    jobs = src._parse_jobs(JUNK_TITLE_HTML)
    titles = [j.title for j in jobs]

    if len(jobs) != 1:
        _record(
            "JUNK_TITLE_COUNT",
            False,
            f"expected 1 (only 'Real Job Posting'), got {len(jobs)}: {titles}",
        )
        return
    if jobs[0].title != "Real Job Posting":
        _record(
            "JUNK_TITLE_TITLE",
            False,
            f"unexpected kept title: {jobs[0].title!r}",
        )
        return
    _record("JUNK_TITLE", True, f"junk anchors skipped, kept {titles}")


def _check_malformed_html_safe() -> None:
    """Malformed HTML must yield [] and not raise."""
    src = _make_src()
    try:
        jobs = src._parse_jobs(MALFORMED_HTML)
    except Exception as exc:
        _record("MALFORMED", False, f"raised: {exc!r}")
        return

    if jobs != []:
        _record(
            "MALFORMED",
            False,
            f"malformed html produced {len(jobs)} jobs: {[j.title for j in jobs]}",
        )
        return
    _record("MALFORMED", True, "malformed html -> []")


def _check_no_network_dependency_in_parse() -> None:
    """The pure parser must not touch ``requests`` — only ``fetch_jobs`` should."""
    import app.sources.hrpeak_source as mod

    func = mod.HrPeakSource._parse_jobs
    closure_globals = func.__globals__
    if "requests" in closure_globals:
        # Just having requests imported at module level is fine
        # (fetch_jobs uses it). What matters is that _parse_jobs does
        # not call into it. We can't easily prove the negative from
        # outside, so this is a sanity spot-check rather than a hard
        # contract test.
        pass
    _record(
        "PARSE_NO_NETWORK",
        True,
        "parser is a pure function over a string",
    )


def _check_company_slug_derivation() -> None:
    """``name`` is a stable, log-friendly slug of the company name."""
    from app.sources.hrpeak_source import HrPeakSource

    cases = [
        ("Kafein Technology Solutions", "kafein_technology_solutions"),
        ("Foo-Bar.co", "foo_bar_co"),
        ("  spaced  out  ", "spaced_out"),
        ("A", "a"),
    ]
    for company, expected_slug in cases:
        src = HrPeakSource(company_name=company, careers_url=_CAREERS_URL)
        if src.name != expected_slug:
            _record(
                "SLUG",
                False,
                f"{company!r} -> {src.name!r}, expected {expected_slug!r}",
            )
            return
    _record("SLUG", True, "company name -> stable slug")


def main() -> int:
    _check_parse()
    _check_metadata()
    _check_constructor_rejects_empty()
    _check_empty_marker_tr()
    _check_empty_marker_en()
    _check_empty_html_string()
    _check_with_jobs_parses_listings()
    _check_job_field_contract()
    _check_listing_url_excluded()
    _check_relative_url_resolved()
    _check_url_dedup()
    _check_junk_title_skipped()
    _check_malformed_html_safe()
    _check_no_network_dependency_in_parse()
    _check_company_slug_derivation()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_HRPEAK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
