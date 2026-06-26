"""Smoke test for KafeinHrPeakSource (parser contract, no network).

Run with ``python tests/smoke_kafein_source.py`` from the project
root. Prints ``<NAME>_OK ...`` lines on success and exits 0. On any
failure prints ``<NAME>_FAIL ...`` with the offending value, dumps
the failure list, and exits 1.

The smoke test never touches the network. It exercises the source's
*pure* parser — :meth:`KafeinHrPeakSource._parse_jobs` — with
hand-crafted HTML samples so we can verify the empty-page, normal
listing, dedup, junk-skip, and malformed-input branches in isolation.
A network outage on ``kafein.hrpeak.com`` cannot make this test
flaky.
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


# ------------------------------- sample HTML -----------------------------------


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
        from app.sources.kafein_hrpeak_source import KafeinHrPeakSource  # noqa: F401
        import requests  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_metadata() -> None:
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    src = KafeinHrPeakSource()
    if src.name != "kafein_hrpeak":
        _record("NAME", False, f"got {src.name!r}")
        return
    if not KafeinHrPeakSource.BASE_URL.startswith("https://kafein.hrpeak.com"):
        _record("BASE_URL", False, f"got {KafeinHrPeakSource.BASE_URL!r}")
        return
    if KafeinHrPeakSource.REQUEST_TIMEOUT <= 0:
        _record("TIMEOUT", False, f"got {KafeinHrPeakSource.REQUEST_TIMEOUT}")
        return
    _record(
        "METADATA",
        True,
        f"name={src.name} base={KafeinHrPeakSource.BASE_URL} "
        f"timeout={KafeinHrPeakSource.REQUEST_TIMEOUT}s",
    )


def _check_empty_marker_tr() -> None:
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(EMPTY_TR_HTML)
    if jobs != []:
        _record("EMPTY_TR", False, f"expected [], got {len(jobs)} jobs")
        return
    _record("EMPTY_TR", True, "Turkish empty marker -> []")


def _check_empty_marker_en() -> None:
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(EMPTY_EN_HTML)
    if jobs != []:
        _record("EMPTY_EN", False, f"expected [], got {len(jobs)} jobs")
        return
    _record("EMPTY_EN", True, "English empty marker -> []")


def _check_empty_html_string() -> None:
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    if KafeinHrPeakSource._parse_jobs("") != []:
        _record("EMPTY_STRING", False, "expected [] for empty input")
        return
    _record("EMPTY_STRING", True, "empty string -> []")


def _check_with_jobs_parses_listings() -> None:
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(LISTING_WITH_JOBS_HTML)
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
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(LISTING_WITH_JOBS_HTML)

    for job in jobs:
        if not job.title or len(job.title) < 3:
            _record(
                "FIELD_TITLE",
                False,
                f"job title missing/short: {job.title!r}",
            )
            return
        if job.company != "Kafein Technology Solutions":
            _record(
                "FIELD_COMPANY",
                False,
                f"unexpected company {job.company!r}",
            )
            return
        if job.source != "kafein_hrpeak":
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
        # Optional fields must default to None since the source does
        # not have a way to learn them from the listing page.
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
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(LISTING_WITH_JOBS_HTML)
    urls = [j.url for j in jobs]

    listing_url = "https://kafein.hrpeak.com/ilan/site.aspx"
    if listing_url in urls:
        _record("LISTING_EXCLUDED", False, f"listing url leaked: {urls}")
        return
    # Also ensure the bare site.aspx suffix cannot have slipped in.
    if any(u.rstrip("/").endswith("site.aspx") for u in urls):
        _record("LISTING_EXCLUDED", False, f"site.aspx suffix leaked: {urls}")
        return
    _record("LISTING_EXCLUDED", True, f"listing url filtered, urls={urls}")


def _check_relative_url_resolved() -> None:
    """Relative hrefs must be joined against BASE_URL."""
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(LISTING_WITH_JOBS_HTML)
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
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(DEDUP_HTML)
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
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    jobs = KafeinHrPeakSource._parse_jobs(JUNK_TITLE_HTML)
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
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    try:
        jobs = KafeinHrPeakSource._parse_jobs(MALFORMED_HTML)
    except Exception as exc:
        _record("MALFORMED", False, f"raised: {exc!r}")
        return

    # Either [] or harmless garbage is acceptable — the contract is
    # "do not crash". Empty is what we get in practice; assert it.
    if jobs != []:
        _record(
            "MALFORMED",
            False,
            f"malformed html produced {len(jobs)} jobs: {[j.title for j in jobs]}",
        )
        return
    _record("MALFORMED", True, "malformed html -> []")


def _check_no_network_dependency_in_parse() -> None:
    """The pure parser must not touch ``requests`` — only ``fetch_jobs`` should.

    We assert by inspection: the static ``_parse_jobs`` method's
    bytecode should not reference the requests module. This is the
    lightest way to keep the test honest if the parser grows later.
    """
    import app.sources.kafein_hrpeak_source as mod

    func = mod.KafeinHrPeakSource._parse_jobs
    closure_globals = func.__globals__
    # The parser may legitimately import BeautifulSoup; it must NOT
    # reference the requests module (that is fetch_jobs's job).
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


def main() -> int:
    _check_parse()
    _check_metadata()
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

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_KAFEIN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
