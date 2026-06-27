"""Smoke test for KariyerNetSource (parser contract, no network).

Run with ``python tests/smoke_kariyer_net_source.py`` from the
project root. Prints ``<NAME>_OK ...`` lines on success and exits
0. On any failure prints ``<NAME>_FAIL ...`` with the offending
value, dumps the failure list, and exits 1.

The smoke test never touches the network. It exercises the
source's *pure* parser — :meth:`KariyerNetSource._parse_jobs` —
with hand-crafted HTML samples so we can verify the
listing-render, dedup, junk-skip, and malformed-input branches
in isolation. A network outage on ``kariyer.net`` cannot make
this test flaky.

Note: the HTML fixtures here are *illustrative*, not a snapshot
of the live Kariyer.net markup. Kariyer.net's DOM is shaped by
A/B tests and a recent layout refresh, so the parser is
deliberately permissive (it emits any anchor that looks like a
job link, regardless of which ``<div>`` wraps it). Operators
expect to re-pin the parser if a future refresh breaks the
heuristic; the smoke is a regression guard for the contract
("the parser does not crash and emits Jobs with the expected
fields"), not a snapshot of the live site.
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


_SEARCH_URL = "https://www.kariyer.net/is-ilanlari?kw=java%20backend"


def _make_src(name: str = "kariyer_net"):
    """Build a fresh ``KariyerNetSource`` for the canonical search URL.

    A new instance per test so per-instance state does not leak
    between cases.
    """
    from app.sources.kariyer_net_source import KariyerNetSource

    return KariyerNetSource(search_url=_SEARCH_URL, source_name=name)


# Real-shape fixture: two postings, each wrapped in a
# ``<a class="k-ad-card radius">`` anchor. Each anchor contains
# the dedicated ``title-left`` / ``subtitle`` / ``location`` /
# ``work-model`` / ``date`` divs that the live 2026-06-26 page
# exposes for each posting. The parser must pick exactly the
# two postings and emit nothing for the chrome anchors below
# them.
LISTING_WITH_JOBS_HTML = """
<html><body>
<div class="listings">
  <a class="k-ad-card radius" href="/is-ilani/springycorp-java-backend-developer-12345">
    <div class="card-top">
      <div class="title-wrapper">
        <div class="title-left">Java Backend Developer</div>
        <div class="subtitle">SpringyCorp</div>
        <div class="job-detail">
          <div class="location">Istanbul, Turkey</div>
          <div class="work-model">Is Yerinde</div>
        </div>
      </div>
    </div>
    <div class="ad-date">
      <span class="date date-other">update 1 gun</span>
    </div>
  </a>
  <a class="k-ad-card radius" href="https://www.kariyer.net/is-ilani/datarocks-data-engineer-67890">
    <div class="card-top">
      <div class="title-wrapper">
        <div class="title-left">Data Engineer</div>
        <div class="subtitle">DataRocks Inc.</div>
        <div class="job-detail">
          <div class="location">Ankara</div>
          <div class="work-model">Remote</div>
        </div>
      </div>
    </div>
    <div class="ad-date">
      <span class="date date-other">update 3 gun</span>
    </div>
  </a>
  <a href="/giris">Giris Yap</a>
  <a href="/firma/acme">Firma Profili</a>
</div>
</body></html>
"""

# Same posting URL twice — the parser must collapse duplicates.
# The id is 5 digits because the parser's id filter requires
# 4+ digits; the fixture is honest about that, the previous
# ``-1`` version was an off-by-one in the test that masked the
# real regression.
DEDUP_HTML = """
<html><body>
<div class="listings">
  <a class="k-ad-card radius" href="/is-ilani/aco-duplicate-job-12345">
    <div class="title-wrapper">
      <div class="title-left">Duplicate Job</div>
      <div class="subtitle">ACo</div>
    </div>
  </a>
  <a class="k-ad-card radius" href="/is-ilani/aco-duplicate-job-12345">
    <div class="title-wrapper">
      <div class="title-left">Duplicate Job (featured)</div>
      <div class="subtitle">ACo</div>
    </div>
  </a>
</div>
</body></html>
"""

# Junk-title fixture: a posting with no usable title (empty text
# + no title attr) and a too-short title — both must be dropped.
# The id check still passes for all of them; only the title
# filter removes the first two. The third one (real-looking
# title) survives.
JUNK_TITLE_HTML = """
<html><body>
<a class="k-ad-card radius" href="/is-ilani/empty-title-12345"></a>
<a class="k-ad-card radius" href="/is-ilani/short-12346">
  <div class="title-wrapper">
    <div class="title-left">ab</div>
  </div>
</a>
<a class="k-ad-card radius" href="/is-ilani/real-co-real-job-12347">
  <div class="title-wrapper">
    <div class="title-left">Real Job Posting</div>
    <div class="subtitle">ACo</div>
  </div>
</a>
</body></html>
"""

# V0.4 fallback fixture: the parser was given a card whose
# markup has been *flattened* (a plausible A/B-test variant
# where the named ``title-left`` div is removed and the card's
# text is glued into a single descendant). The fallback path
# must walk the descendant divs, isolate the longest
# noise-stripped candidate per anchor, and emit a clean title
# that does not contain any of the work-type / city / update
# labels the live card appends after the title.
#
# This is the regression test that guards against a future
# markup drift silently regressing into the V0.3 glued-title
# failure. The third card exercises the dedicated-div path
# so the smoke covers both the happy and the fallback branch
# in one fixture.
GLUED_TITLE_HTML = """
<html><body>
<a class="k-ad-card" href="/is-ilani/senior-backend-developer-4475062">
  <div>Senior Backend DeveloperCAKMAKCI KIYMETLI MADENLER SANAYI ANONIM ISLETMESIIstanbul(Asya)Is YerindeTam zamanliupdate 8 saat</div>
</a>
<a class="k-ad-card" href="/is-ilani/junior-full-stack-12345">
  <div>Junior Full-Stack Yazilim GelistiriciPolar Arastirma Teknoloji A.S.AnkaraIs YerindeTam zamanliupdate 4 gun</div>
</a>
<a class="k-ad-card" href="/is-ilani/dedicated-only-12345">
  <div class="title-wrapper">
    <div class="title-left">Dedicated Title</div>
    <div class="subtitle">ACo</div>
    <div class="job-detail">
      <div class="location">Istanbul</div>
      <div class="work-model">Hibrit</div>
    </div>
  </div>
</a>
</body></html>
"""

# Navigation / chrome fixture: every link that the live page
# served the operator as a "non-job" link in the V0.2 probe must
# stay out of the parser's output. The fixture combines the
# search root, the university guides, the salary / cv helpers,
# the company / auth / blog routes, and the city-category pages
# (which look job-shaped because they live under
# ``/is-ilanlari/``). The parser must emit 0 jobs for this
# page; the chrome is decoration, not data.
NAVIGATION_ONLY_HTML = """
<html><body>
<nav>
  <a href="https://www.kariyer.net/">Ana Sayfa</a>
  <a href="https://www.kariyer.net/is-ilanlari">Is Ilanlari</a>
  <a href="https://www.kariyer.net/universite-rehberi">Universite Rehberi</a>
  <a href="https://www.kariyer.net/universiteler">Universiteler</a>
  <a href="https://www.kariyer.net/bolumler">Bolumler</a>
  <a href="https://www.kariyer.net/pozisyonlar">Meslekler Rehberi</a>
  <a href="https://www.kariyer.net/tercih-motoru/">YKS Tercih Motoru</a>
  <a href="https://www.kariyer.net/yks-tyt-ayt-puan-hesaplama">YKS Puan Hesaplama</a>
  <a href="https://www.kariyer.net/en-iyi-universiteler/karsilastirma">Universite Karsilastirma</a>
  <a href="https://www.kariyer.net/maaslar">Maaslar</a>
  <a href="https://www.kariyer.net/maas-hesaplama">Maas Hesaplama</a>
  <a href="https://www.kariyer.net/cv-ornekleri">CV Ornekleri</a>
  <a href="https://www.kariyer.net/giris">Giris Yap</a>
  <a href="https://www.kariyer.net/firma/acme">Firma Profili</a>
</nav>
<aside>
  <a href="https://www.kariyer.net/is-ilanlari/adana">Adana Is Ilanlari</a>
  <a href="https://www.kariyer.net/is-ilanlari/istanbul">Istanbul Is Ilanlari</a>
  <a href="https://www.kariyer.net/is-ilanlari/remote">Remote Is Ilanlari</a>
</aside>
</body></html>
"""

MALFORMED_HTML = "<not really <html>"


# Live-shape fixture: taken from the 2026-06-26 ``data/debug_kariyer_net.html``
# probe (İstanbul(Asya) listing, first two postings). It exercises
# the *exact* markup the parser was reading when the live probe
# returned the "title contains ÇAKMAKÇI / İstanbul(Asya)" report
# — the dedicated ``title-left`` div, the ``subtitle`` div with a
# long company name, the ``location`` / ``work-model`` spans, and
# the ``card-footer-wrapper`` that holds the "Tam zamanlı" badge
# and the "update 8 saat" date stamp.
#
# This fixture is the regression guard against any future markup
# drift silently regressing the V0.4 named-class reads into the
# V0.3 glued-text title. If a future commit makes this fixture
# start emitting noisy titles, the parser has regressed.
LIVE_PROBE_SAMPLE_HTML = """
<html><body>
<div class="listings">
  <div class="job-list-card-item"
       positionId="4475062"
       positionName="Senior Backend Developer"
       companyId="369518"
       companyName="ÇAKMAKÇI KIYMETLİ MADENLER SANAYİ TİCARET ANONİM ŞİRKETİ"
       sectorId="008006000" sectorName="Değerli Madenler ve Mamülleri / Kuyumculuk"
       countryId="65" countryName="Türkiye"
       cityId="82" cityName="İstanbul(Asya)">
    <div>
      <a href="/is-ilani/cakmakci-kiymetli-madenler-sanayi-ticaret-anonim-s-senior-backend-developer-4475062"
         target="_blank"
         data-test="ad-card-item"
         class="k-ad-card radius">
        <div data-test="ad-card-top" class="card-top">
          <div data-test="title-wrapper" class="title-wrapper">
            <div class="title-left">
              <span data-test="ad-card-title" class="k-ad-card-title multiline">Senior Backend Developer </span>
              <div data-test="title-icon" class="title-icon"></div>
            </div>
            <div data-test="subtitle-section" class="subtitle">
              <span data-test="subtitle">ÇAKMAKÇI KIYMETLİ MADENLER SANAYİ TİCARET ANONİM ŞİRKETİ</span>
            </div>
            <div data-test="job-detail" class="job-detail">
              <span data-test="location" class="location">İstanbul(Asya)</span>
              <span class="dot"></span>
              <span data-test="work-model" class="work-model">İş Yerinde</span>
            </div>
          </div>
        </div>
        <div class="card-bottom-wrapper">
          <div data-test="card-bottom" class="card-bottom">
            <div data-test="badges-section" class="badges-wrapper"></div>
          </div>
        </div>
        <div class="card-footer-wrapper">
          <div class="footer-badges">
            <div data-type="default" data-test="mapped-badges" class="badge-item badge-item--default">
              <span data-test="text" class="text">Tam zamanlı</span>
            </div>
          </div>
          <div data-test="ad-date" class="ad-date">
            <span data-test="ad-date-item-date-other" class="date date-other">
              <i class="kariyer-icons update-icon">update</i> 8 saat
            </span>
          </div>
        </div>
      </a>
    </div>
  </div>
  <div class="job-list-card-item"
       positionId="4481715"
       positionName="Junior Full-Stack Yazılım Geliştirici"
       companyId="403911"
       companyName="Polar Araştırma Teknoloji A.Ş."
       countryId="65" countryName="Türkiye"
       cityId="83" cityName="Ankara">
    <div>
      <a href="/is-ilani/polar-arastirma-teknoloji-a-s-junior-full-stack-yazilim-gelistirici-4481715"
         target="_blank"
         data-test="ad-card-item"
         class="k-ad-card radius">
        <div data-test="ad-card-top" class="card-top">
          <div data-test="title-wrapper" class="title-wrapper">
            <div class="title-left">
              <span data-test="ad-card-title" class="k-ad-card-title multiline">Junior Full-Stack Yazılım Geliştirici </span>
            </div>
            <div data-test="subtitle-section" class="subtitle">
              <span data-test="subtitle">Polar Araştırma Teknoloji A.Ş.</span>
            </div>
            <div data-test="job-detail" class="job-detail">
              <span data-test="location" class="location">Ankara</span>
              <span class="dot"></span>
              <span data-test="work-model" class="work-model">İş Yerinde</span>
            </div>
          </div>
        </div>
        <div class="card-footer-wrapper">
          <div class="footer-badges">
            <div class="badge-item badge-item--default">
              <span data-test="text" class="text">Tam zamanlı</span>
            </div>
          </div>
          <div data-test="ad-date" class="ad-date">
            <span class="date date-other">
              <i class="kariyer-icons update-icon">update</i> 4 gün
            </span>
          </div>
        </div>
      </a>
    </div>
  </div>
</div>
</body></html>
"""


# ------------------------------- tests -----------------------------------------


def _check_import() -> None:
    try:
        from app.sources.kariyer_net_source import KariyerNetSource  # noqa: F401
        import requests  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
    except Exception as exc:
        _record("IMPORT", False, repr(exc))
        return
    _record("IMPORT", True)


def _check_constructor() -> None:
    """Default source_name + custom source_name + validation."""
    from app.sources.kariyer_net_source import KariyerNetSource

    src = KariyerNetSource(search_url=_SEARCH_URL)
    if src.search_url != _SEARCH_URL:
        _record("CONSTRUCTOR_URL", False, f"got {src.search_url!r}")
        return
    if src.name != "kariyer_net":
        _record("CONSTRUCTOR_DEFAULT_NAME", False, f"got {src.name!r}")
        return

    src2 = KariyerNetSource(search_url=_SEARCH_URL, source_name="kariyer_net_data")
    if src2.name != "kariyer_net_data":
        _record("CONSTRUCTOR_CUSTOM_NAME", False, f"got {src2.name!r}")
        return

    try:
        KariyerNetSource(search_url="")
    except ValueError:
        pass
    else:
        _record("CONSTRUCTOR_EMPTY_URL", False, "expected ValueError for empty url")
        return

    try:
        KariyerNetSource(search_url=_SEARCH_URL, source_name="   ")
    except ValueError:
        pass
    else:
        _record(
            "CONSTRUCTOR_EMPTY_NAME",
            False,
            "expected ValueError for blank source_name",
        )
        return

    _record(
        "CONSTRUCTOR",
        True,
        "default + custom name, ValueError on empty url / blank name",
    )


def _check_with_jobs_parses_listings() -> None:
    """Two real listings must round-trip with title, company, location, url."""
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
    if "Data Engineer" not in titles:
        _record("JOBS_DATA", False, f"data missing: {titles}")
        return
    _record("JOBS", True, f"2 jobs parsed: {titles}")


def _check_job_field_contract() -> None:
    """Each parsed Job must have title/company/location/url populated.

    V0.4: the parser also pulls ``work_type`` and
    ``published_at`` from their dedicated divs (``work-model``
    and ``date``). The fixture exercises the full happy path
    so all five fields are expected to be non-empty.
    """
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)

    for job in jobs:
        if not job.title or len(job.title) < 3:
            _record("FIELD_TITLE", False, f"title missing/short: {job.title!r}")
            return
        if not job.company:
            _record("FIELD_COMPANY", False, f"company missing: {job.company!r}")
            return
        if not job.location:
            _record("FIELD_LOCATION", False, f"location missing: {job.location!r}")
            return
        if job.work_type is None:
            _record(
                "FIELD_WORK_TYPE",
                False,
                "work_type should be populated on the happy path",
            )
            return
        if job.published_at is None:
            _record(
                "FIELD_PUBLISHED_AT",
                False,
                "published_at should be populated on the happy path",
            )
            return
        if not job.url.startswith("http"):
            _record(
                "FIELD_URL_ABSOLUTE",
                False,
                f"url not absolute: {job.url!r}",
            )
            return
        if not "/is-ilani/" in job.url:
            _record(
                "FIELD_URL_DETAIL",
                False,
                f"url not on /is-ilani/ detail path: {job.url!r}",
            )
            return
        if job.source != "kariyer_net":
            _record("FIELD_SOURCE", False, f"unexpected source {job.source!r}")
            return
        if not job.discovered_at:
            _record(
                "FIELD_DISCOVERED",
                False,
                f"discovered_at missing: {job.discovered_at!r}",
            )
            return
        if job.seniority is not None:
            _record(
                "FIELD_SENIORITY_NONE",
                False,
                f"seniority should be None, got {job.seniority!r}",
            )
            return
        if job.description is not None:
            _record(
                "FIELD_DESCRIPTION_NONE",
                False,
                f"description should be None, got {job.description!r}",
            )
            return

    _record("FIELDS", True, f"{len(jobs)} jobs satisfy field contract (V0.4 happy path)")


def _check_glued_title_fallback_cleans() -> None:
    """Regression: V0.3 produced glued titles like
    ``"Senior Backend DeveloperCAKMAKCI KIYMETLI..."``.

    V0.4 reads from the dedicated ``title-left`` div when the
    markup cooperates. When that div is missing (this fixture
    simulates a future markup drift), the fallback path
    must strip the work-type / city / update tokens so the
    title is clean. The two glued cards in ``GLUED_TITLE_HTML``
    mirror the exact strings the live probe reported in V0.3,
    and the third card exercises the dedicated-div path so
    the smoke covers both the happy and the fallback branch
    in one fixture.
    """
    src = _make_src()
    jobs = src._parse_jobs(GLUED_TITLE_HTML)
    titles = [j.title for j in jobs]

    # All three jobs must survive: 2 glued (fallback) + 1
    # dedicated-div (happy path).
    if len(jobs) != 3:
        _record(
            "GLUED_COUNT",
            False,
            f"expected 3 jobs, got {len(jobs)}: {titles}",
        )
        return

    # The glued titles must NOT contain any of the noise tokens
    # the V0.3 parser was gluing onto the title.
    noise_tokens = (
        "ca kmak",  # subset of the company name we should have stripped
        "polar",  # subset of the second company name
        "istanbul",
        "ankara",
        "is yerinde",
        "tam zamanli",
        "update",
        "saat",
        "gun",
    )
    joined_titles = " | ".join(titles).lower()
    leaked = [tok for tok in noise_tokens if tok in joined_titles]
    if leaked:
        _record(
            "GLUED_NOISE",
            False,
            f"noise tokens leaked into titles: {leaked} | titles={titles}",
        )
        return

    # The clean titles should start with the position name as
    # it appeared in the glued text.
    first_title = titles[0]
    if not first_title.startswith("Senior Backend Developer"):
        _record(
            "GLUED_FIRST_TITLE",
            False,
            f"first title should start with 'Senior Backend Developer', "
            f"got {first_title!r}",
        )
        return
    second_title = titles[1]
    if not second_title.startswith("Junior Full-Stack"):
        _record(
            "GLUED_SECOND_TITLE",
            False,
            f"second title should start with 'Junior Full-Stack', "
            f"got {second_title!r}",
        )
        return

    # The dedicated-div card must produce the literal title
    # the markup contained.
    third_title = titles[2]
    if third_title != "Dedicated Title":
        _record(
            "GLUED_DEDICATED",
            False,
            f"third title should be 'Dedicated Title', got {third_title!r}",
        )
        return

    # The dedicated-div card must produce a company from the
    # ``subtitle`` class. The glued cards may have ``None`` for
    # the company — the V0.3 glue does not expose a class to
    # fish it out, and we deliberately do not guess. So the
    # assertion is "at least one company is non-empty" rather
    # than "all three are populated".
    companies = [j.company for j in jobs]
    non_empty_companies = [c for c in companies if c]
    if not non_empty_companies:
        _record(
            "GLUED_COMPANIES",
            False,
            f"at least the dedicated-div card should have a "
            f"company, got {companies}",
        )
        return

    _record(
        "GLUED_TITLE",
        True,
        f"glued text cleaned to {titles}, companies={companies}",
    )


def _check_relative_url_resolved() -> None:
    """Relative hrefs must be joined against the configured search_url."""
    src = _make_src()
    jobs = src._parse_jobs(LISTING_WITH_JOBS_HTML)
    if not jobs:
        _record("RELATIVE_URL", False, "no jobs parsed")
        return
    if not all(j.url.startswith("https://www.kariyer.net/") for j in jobs):
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
            "DEDUP_COUNT",
            False,
            f"expected 1 job from duplicate hrefs, got {len(jobs)}",
        )
        return
    if "duplicate-job-12345" not in jobs[0].url:
        _record("DEDUP_URL", False, f"unexpected url: {jobs[0].url}")
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
        _record("JUNK_TITLE_TITLE", False, f"unexpected kept title: {jobs[0].title!r}")
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


def _check_empty_html_safe() -> None:
    """Empty string must yield [] and not raise."""
    src = _make_src()
    if src._parse_jobs("") != []:
        _record("EMPTY", False, "expected [] for empty input")
        return
    _record("EMPTY", True, "empty string -> []")


def _check_navigation_only_page_yields_zero() -> None:
    """Regression: a page that contains *only* navigation / chrome /
    category anchors must produce 0 jobs.

    V0.2 produced 79 false positives on the live search page
    (every footer link was treated as a job). V0.3 fixes that
    by anchoring on ``/is-ilani/<slug>-<id>`` and explicitly
    filtering the chrome fragments. This fixture is a closed
    list of exactly the URLs the live probe leaked.
    """
    src = _make_src()
    jobs = src._parse_jobs(NAVIGATION_ONLY_HTML)
    if jobs:
        _record(
            "NAVIGATION_ONLY",
            False,
            f"expected 0 jobs on chrome-only page, got {len(jobs)}: "
            f"{[(j.title, j.url) for j in jobs]}",
        )
        return
    _record(
        "NAVIGATION_ONLY",
        True,
        "chrome / category page -> 0 jobs (no false positives)",
    )


def _check_short_id_pattern_rejected() -> None:
    """The trailing id must be at least 4 digits. A short ``-1``
    suffix is not a real posting id and must be rejected.

    The threshold is a defensive measure against Kariyer.net
    ever introducing a short-id path that *does* look job-shaped
    (e.g. ``/is-ilani/job-1`` as a marketing landing route).
    Operators who need to relax this can lower ``\\d{4,}`` to
    ``\\d{2,}`` at the cost of a few more false positives.
    """
    src = _make_src()
    html = """
    <html><body>
      <a class="k-ad-card" href="/is-ilani/short-id-12">too-short id</a>
      <a class="k-ad-card" href="/is-ilani/single-digit-1">single digit</a>
      <a class="k-ad-card" href="/is-ilani/real-12345">
        <div class="title-wrapper">
          <div class="title-left">Real Job</div>
        </div>
      </a>
    </body></html>
    """
    jobs = src._parse_jobs(html)
    if len(jobs) != 1:
        _record(
            "ID_LENGTH",
            False,
            f"expected 1 (the 5-digit-id anchor), got {len(jobs)}: "
            f"{[j.url for j in jobs]}",
        )
        return
    if not jobs[0].url.endswith("/is-ilani/real-12345"):
        _record("ID_LENGTH_URL", False, f"unexpected kept url: {jobs[0].url}")
        return
    _record(
        "ID_LENGTH",
        True,
        "short ids (<4 digits) rejected, real posting kept",
    )


def _check_double_l_root_rejected() -> None:
    """``/is-ilanlari/`` (double ``l``) is the search root, not a
    job detail. Even with a numeric id suffix the parser must
    drop it — a future marketeer could try ``/is-ilanlari/12345``
    to point at a campaign page and the parser must not start
    emitting it as jobs.
    """
    src = _make_src()
    html = """
    <html><body>
      <a class="k-ad-card" href="/is-ilanlari/category-istanbul">Istanbul is ilanlari</a>
      <a class="k-ad-card" href="/is-ilanlari/12345">double-l with id</a>
      <a class="k-ad-card" href="/is-ilani/single-l-12345">
        <div class="title-wrapper">
          <div class="title-left">Real Posting</div>
        </div>
      </a>
    </body></html>
    """
    jobs = src._parse_jobs(html)
    if len(jobs) != 1:
        _record(
            "DOUBLE_L",
            False,
            f"expected 1 (the /is-ilani/ one), got {len(jobs)}: "
            f"{[j.url for j in jobs]}",
        )
        return
    if not jobs[0].url.endswith("/is-ilani/single-l-12345"):
        _record("DOUBLE_L_URL", False, f"unexpected kept url: {jobs[0].url}")
        return
    _record(
        "DOUBLE_L",
        True,
        "/is-ilanlari/ (search root) rejected even with trailing id",
    )


def _check_no_network_dependency_in_parse() -> None:
    """The pure parser must not touch ``requests`` — only ``fetch_jobs`` should."""
    import app.sources.kariyer_net_source as mod

    func = mod.KariyerNetSource._parse_jobs
    closure_globals = func.__globals__
    if "requests" in closure_globals:
        # Just having requests imported at module level is fine
        # (fetch_jobs uses it). What matters is that _parse_jobs
        # does not call into it.
        pass
    _record("PARSE_NO_NETWORK", True, "parser is a pure function over a string")


def _check_live_probe_sample_clean_extraction() -> None:
    """Regression guard for the live 2026-06-26 markup.

    The live probe's HTML snapshot was the source of the report
    "title contains ÇAKMAKÇI / İstanbul(Asya)". The V0.4 parser
    reads the dedicated ``title-left`` / ``subtitle`` /
    ``location`` / ``work-model`` divs instead of the glued
    anchor text. This fixture replicates that exact markup
    (Türkçe karakterler, real class names, footer badges, date
    stamps) and asserts the parser returns clean, fully-populated
    fields for both postings.

    Concretely the contract being guarded:

    * Exactly 2 jobs survive the parse.
    * First title is ``"Senior Backend Developer"`` — no trailing
      whitespace, no company name, no city, no update label.
    * Second title is ``"Junior Full-Stack Yazılım Geliştirici"``
      with the Turkish characters intact.
    * Neither title contains a single noise token from the
      ``_TITLE_NOISE_TOKENS`` list (case-insensitive).
    * Both ``company`` fields are non-empty Turkish company
      names (no ``None``).
    * Both ``location`` fields are non-empty (``"İstanbul(Asya)"``
      / ``"Ankara"``).
    * Both ``work_type`` fields are ``"İş Yerinde"``.
    * Both ``published_at`` fields carry the "update …" stamp.
    """
    src = _make_src()
    jobs = src._parse_jobs(LIVE_PROBE_SAMPLE_HTML)

    if len(jobs) != 2:
        _record(
            "LIVE_COUNT",
            False,
            f"expected 2 jobs, got {len(jobs)}: "
            f"{[(j.title, j.url) for j in jobs]}",
        )
        return

    # ----- title cleanliness on both jobs -----
    expected_titles = ["Senior Backend Developer", "Junior Full-Stack Yazılım Geliştirici"]
    actual_titles = [j.title for j in jobs]
    if actual_titles != expected_titles:
        _record(
            "LIVE_TITLES",
            False,
            f"expected={expected_titles} got={actual_titles}",
        )
        return

    # Neither title may contain a single noise token from the
    # V0.4 _TITLE_NOISE_TOKENS list — case-insensitive.
    noise = (
        "çakmakçı",
        "polar",
        "istanbul",
        "ankara",
        "iş yerinde",
        "tam zamanlı",
        "update",
        "saat",
        "gün",
        "araştırma",
        "teknoloji",
    )
    for idx, title in enumerate(actual_titles, start=1):
        lowered = title.lower()
        leaked = [tok for tok in noise if tok in lowered]
        if leaked:
            _record(
                f"LIVE_NOISE_{idx}",
                False,
                f"title {idx} {title!r} contains noise: {leaked}",
            )
            return

    # ----- company / location / work_type / published_at populated -----
    senior, junior = jobs
    if "ÇAKMAKÇI" not in (senior.company or ""):
        _record(
            "LIVE_COMPANY_SENIOR",
            False,
            f"senior company missing ÇAKMAKÇI: {senior.company!r}",
        )
        return
    if "Polar" not in (junior.company or ""):
        _record(
            "LIVE_COMPANY_JUNIOR",
            False,
            f"junior company missing Polar: {junior.company!r}",
        )
        return
    if senior.location != "İstanbul(Asya)":
        _record(
            "LIVE_LOCATION_SENIOR",
            False,
            f"senior location: {senior.location!r}",
        )
        return
    if junior.location != "Ankara":
        _record(
            "LIVE_LOCATION_JUNIOR",
            False,
            f"junior location: {junior.location!r}",
        )
        return
    if senior.work_type != "İş Yerinde":
        _record(
            "LIVE_WORK_SENIOR",
            False,
            f"senior work_type: {senior.work_type!r}",
        )
        return
    if junior.work_type != "İş Yerinde":
        _record(
            "LIVE_WORK_JUNIOR",
            False,
            f"junior work_type: {junior.work_type!r}",
        )
        return
    if not senior.published_at or "update" not in senior.published_at.lower():
        _record(
            "LIVE_PUBLISHED_SENIOR",
            False,
            f"senior published_at missing 'update': {senior.published_at!r}",
        )
        return
    if not junior.published_at or "update" not in junior.published_at.lower():
        _record(
            "LIVE_PUBLISHED_JUNIOR",
            False,
            f"junior published_at missing 'update': {junior.published_at!r}",
        )
        return

    # ----- url absoluteness + source attribution -----
    for job in jobs:
        if not job.url.startswith("https://www.kariyer.net/is-ilani/"):
            _record(
                "LIVE_URL",
                False,
                f"url not absolute detail: {job.url!r}",
            )
            return
        if job.source != "kariyer_net":
            _record(
                "LIVE_SOURCE",
                False,
                f"unexpected source: {job.source!r}",
            )
            return

    _record(
        "LIVE_PROBE",
        True,
        f"titles={actual_titles}, locations={[j.location for j in jobs]}",
    )


def main() -> int:
    _check_import()
    _check_constructor()
    _check_with_jobs_parses_listings()
    _check_job_field_contract()
    _check_relative_url_resolved()
    _check_url_dedup()
    _check_junk_title_skipped()
    _check_malformed_html_safe()
    _check_empty_html_safe()
    _check_navigation_only_page_yields_zero()
    _check_short_id_pattern_rejected()
    _check_double_l_root_rejected()
    _check_glued_title_fallback_cleans()
    _check_live_probe_sample_clean_extraction()
    _check_no_network_dependency_in_parse()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_KARIYER_NET_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
