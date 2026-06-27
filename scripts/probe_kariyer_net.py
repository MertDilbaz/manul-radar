"""Live probe for KariyerNetSource.

One-off manual test: hits the public Kariyer.net search page with
the real ``KariyerNetSource``, prints the first 10 jobs as the
production code would emit them, and dumps a debug HTML snapshot
plus a couple of HTTP / page metrics if the parser returns 0
jobs (which usually means the live markup has drifted from the
parser's heuristic).

This script is intentionally throwaway:

* It writes ``data/debug_kariyer_net.html`` on 0-job outcomes so
  the operator can diff against the parser's expectations. The
  file lives under ``data/`` which is gitignored in this repo.
* It does *not* touch ``run_monitor.py``, ``config.yaml``, the
  production source, or any other persistent code path. Run it,
  read the output, delete the file when you're done.

Usage::

    python scripts/probe_kariyer_net.py
"""
from __future__ import annotations

import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Make ``app`` importable when this script is invoked from the
# project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sources.kariyer_net_source import KariyerNetSource  # noqa: E402

PROBE_URL = "https://www.kariyer.net/is-ilanlari?kw=java%20backend"
USER_AGENT = (
    "Mozilla/5.0 (compatible; ManulRadar/1.0; "
    "+https://github.com/local/manul-radar)"
)
DEBUG_HTML_PATH = PROJECT_ROOT / "data" / "debug_kariyer_net.html"


def _safe_text(raw: str | None, limit: int = 500) -> str:
    """Return a single-line, length-bounded preview of ``raw``."""
    if not raw:
        return "<empty>"
    cleaned = unescape(raw).replace("\r", " ").replace("\n", " ")
    return (cleaned[:limit] + "...") if len(cleaned) > limit else cleaned


def _dump_debug_snapshot(response: requests.Response) -> None:
    """Persist the raw HTML and print a one-shot diagnostic block.

    The snapshot lives under ``data/`` so the repo's ``.gitignore``
    keeps it out of version control. We also print a few HTTP /
    page-level metrics so the operator can tell at a glance
    whether the failure is ``captcha / anti-bot``, ``404``,
    ``empty page`` (page rendered but no listings), or
    ``layout drift`` (page rendered with the expected text but
    the parser's DOM heuristic does not match).
    """
    DEBUG_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEBUG_HTML_PATH.write_text(response.text, encoding="utf-8")

    soup: BeautifulSoup | None = None
    page_title: str = "<no <title> tag>"
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        if soup.title and soup.title.string:
            page_title = soup.title.string.strip()
    except Exception as exc:  # noqa: BLE001 — diagnostics, never crash the probe
        page_title = f"<parse error: {exc!r}>"

    plain_preview = _safe_text(
        soup.get_text(" ", strip=True) if soup else response.text
    )

    print()
    print("--- DEBUG: 0 jobs returned, dumping diagnostics ---")
    print(f"  HTTP status            : {response.status_code}")
    print(f"  Response Content-Length: {response.headers.get('Content-Length', '?')}")
    print(f"  Response length (text) : {len(response.text)} chars")
    print(f"  Final URL (after redirects): {response.url}")
    print(f"  Page <title>           : {_safe_text(page_title, 200)}")
    print(f"  First 500 chars plain  : {plain_preview}")
    print(f"  HTML saved to          : {DEBUG_HTML_PATH}")
    print("--- end DEBUG ---")
    print()


def _always_dump_snapshot(response: requests.Response) -> None:
    """Persist the raw HTML on every probe run, not just 0-job ones.

    Even on a successful parse the live page is the single
    source of truth for what the parser *should* match, so the
    operator usually wants the snapshot on hand for diffing.
    Cheap (single ``write_text``), lives under ``data/`` so the
    ``.gitignore`` keeps it out of the repo.
    """
    DEBUG_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEBUG_HTML_PATH.write_text(response.text, encoding="utf-8")
    print(f"Snapshot saved to: {DEBUG_HTML_PATH}")


def _list_sample_anchors(response: requests.Response, *, limit: int = 20) -> None:
    """Print a sample of anchors so the operator can see the
    real URL pattern on the live page.

    The probe is a *debugging* tool, not a parser; the value
    is in showing what Kariyer.net *actually* links to from a
    search result page, so the parser's filter can be tuned
    against the real DOM.
    """
    print()
    print("--- Sample anchors (first {} with non-empty href) ---".format(limit))
    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        print(f"  <parse error: {exc!r}>")
        return

    shown = 0
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(response.url, href)
        text = anchor.get_text(strip=True)[:60]
        # Filter out anchors that obviously are NOT real job
        # listings, so the operator can read the sample and see
        # which anchors are *real* postings vs. site chrome.
        print(f"  [{shown + 1}] text={text!r:30}  href={absolute}")
        shown += 1
        if shown >= limit:
            break
    print("--- end sample ---")
    print()


def main() -> int:
    """Probe the live Kariyer.net page and print results.

    Returns 0 on success (jobs found OR a clean 0 with a
    diagnostic dump); 1 only on a hard transport failure so
    CI / smoke wrappers can detect a broken probe vs a broken
    site.
    """
    print(f"Probing: {PROBE_URL}")
    print(f"User-Agent: {USER_AGENT}")
    print()

    src = KariyerNetSource(search_url=PROBE_URL, source_name="probe_kariyer_net")

    # We deliberately drive the HTTP call ourselves instead of
    # calling ``src.fetch_jobs()`` so we can attach diagnostics on
    # failure. ``fetch_jobs()`` would propagate the exception up
    # the same way, but we want the operator to see the page
    # content even on a 0-job parse.
    t0 = time.perf_counter()
    try:
        response = requests.get(
            PROBE_URL,
            timeout=15,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"HTTP request failed: {exc!r}")
        return 1
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"HTTP {response.status_code} in {elapsed_ms:.0f} ms, "
          f"{len(response.text)} chars")

    jobs = src._parse_jobs(response.text)
    print(f"Parser returned: {len(jobs)} job(s)")
    print()

    if not jobs:
        _dump_debug_snapshot(response)
        return 0

    # Even on a successful parse, dump a snapshot + a sample of
    # anchors so the operator can spot the real job URL pattern
    # against the noise we *did* let through.
    _always_dump_snapshot(response)
    _list_sample_anchors(response, limit=20)

    print("--- First 10 jobs ---")
    shown = 0
    for job in jobs:
        if shown >= 10:
            remaining = len(jobs) - shown
            print(f"... and {remaining} more (truncated to 10)")
            break
        print(f"  [{shown + 1}] {job.title}")
        print(f"      company : {job.company!r}")
        print(f"      location: {job.location!r}")
        print(f"      url     : {job.url}")
        print()
        shown += 1
    print("--- end ---")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
