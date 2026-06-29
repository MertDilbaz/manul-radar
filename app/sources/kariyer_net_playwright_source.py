"""Kariyer.net source backed by Playwright (browser-rendered HTML).

The sibling :mod:`app.sources.kariyer_net_source` fetches the Kariyer.net
public listing page with a plain ``requests.get``. That works when the site
is reachable directly, but Kariyer.net sits behind Cloudflare and will
serve a ``403`` / challenge interstitial to anything that looks like a
bot. A bare ``requests`` call cannot execute the JavaScript challenge,
so the HTTP source returns ``[]`` on every protected page.

This source solves that by rendering the page in a real headless
Chromium via `Playwright <https://playwright.dev/python/>`_. A real
browser executes the challenge script, settles the Cloudflare check, and
produces the same HTML a human visitor sees — including the
``<a class="k-ad-card radius">`` anchors the parser already knows how to
read.

Key design decision: **the HTML parsing logic is not duplicated here**.
``KariyerNetPlaywrightSource`` inherits from :class:`KariyerNetSource`
and reuses every pure-parsing method (``_parse_jobs``,
``_looks_like_job_link``, ``_extract_title``, ``_extract_class_text``,
``_extract_date_text`` …). Only :meth:`fetch_jobs` is overridden — it
swaps the network backend from ``requests`` to Playwright. This means a
fix to the card parser lands in both sources at once, and the smoke
tests written against ``KariyerNetSource._parse_jobs`` cover this class
too.

Error handling is deliberately defensive: if Playwright is missing, if
the browser fails to launch, or if the page never settles,
:meth:`fetch_jobs` logs a warning and returns ``[]`` rather than
raising. A hostile / redesigned page therefore cannot take the monitor
service down with it; the source simply reports no jobs for this run and
the operator sees the warning in the log.
"""
from __future__ import annotations

from app.models.job import Job
from app.sources.kariyer_net_source import KariyerNetSource
from app.utils.logger import logger


# A realistic desktop Chrome User-Agent. Kariyer.net / Cloudflare
# fingerprint the UA among many other signals; the requests-based source
# uses a custom bot UA which is one of the things that gets it blocked.
# Matching a current stable Chrome release is the cheapest signal we can
# send to look like a real visitor.
_PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class KariyerNetPlaywrightSource(KariyerNetSource):
    """Fetch Kariyer.net listings via a headless Chromium, parse as usual.

    This is a drop-in Cloudflare-bypassing replacement for
    :class:`KariyerNetSource`. It shares the identical parsing logic
    (title / company / location / work-type / published_at extraction
    from ``k-ad-card`` anchors) and only changes how the raw HTML is
    obtained: a real browser renders the page instead of a single HTTP
    GET.

    Args:
        search_url: Absolute URL of a public Kariyer.net search /
            listing page (typically ending in ``/is-ilanlari?...``).
            Required.
        source_name: ``name`` attribute stamped on every emitted
            ``Job.source``. Defaults to ``"kariyer_net_pw"`` so the
            Playwright-backed source is distinguishable from the
            requests-backed one in logs and persistence keys even when
            both are configured.
        headless: Whether to run Chromium headless. Defaults to
            ``True`` (the only sane default for CI / scheduling). Set
            to ``False`` only for local debugging where you want to
            watch the browser solve the challenge.
        timeout: Per-navigation timeout in milliseconds, forwarded to
            Playwright's ``page.goto`` and the ``networkidle`` wait.
            Defaults to ``30000`` (30 s), enough for the Cloudflare
            challenge to clear on a warm connection.
    """

    def __init__(
        self,
        search_url: str,
        source_name: str = "kariyer_net_pw",
        headless: bool = True,
        timeout: int = 30000,
    ) -> None:
        """Store Playwright options, then defer to the base constructor.

        The base ``KariyerNetSource.__init__`` validates ``search_url``
        and ``source_name`` (non-empty, etc.) and sets ``self.search_url``
        / ``self.name``. We only add the Playwright-specific knobs.
        """
        super().__init__(search_url=search_url, source_name=source_name)
        self.headless = headless
        self.timeout = timeout

    def fetch_jobs(self) -> list[Job]:
        """Render the listing page in Chromium and parse the result.

        Steps:

        1. Launch headless Chromium with a realistic User-Agent and a
           desktop viewport.
        2. Navigate to ``self.search_url`` and wait for
           ``networkidle`` so the Cloudflare challenge has time to
           resolve and the job cards have time to render.
        3. Capture the fully rendered DOM via ``page.content()``.
        4. Close the browser (always, even on failure).
        5. Hand the HTML to the inherited :meth:`_parse_jobs`, which
           walks the ``k-ad-card`` anchors exactly as the requests-based
           source does.

        Returns ``[]`` on any Playwright / rendering failure — the
        monitor service treats an empty list as "no jobs this run" and
        keeps going. A warning is logged so the operator can see the
        source is misbehaving rather than genuinely empty.
        """
        html = self._render_html()
        if not html:
            return []
        return self._parse_jobs(html)

    # ------------------------------------------------------------------
    # Browser rendering — kept in its own method so it is easy to mock
    # in tests and so ``fetch_jobs`` stays a thin orchestrator.
    # ------------------------------------------------------------------

    def _render_html(self) -> str:
        """Launch Chromium, navigate, and return the rendered page HTML.

        Returns the empty string on *any* failure (Playwright not
        installed, launch error, navigation timeout, page error) after
        logging a warning. Callers treat ``""`` as "no HTML to parse".
        """
        try:
            # Import inside the method so the module can be imported in
            # environments where Playwright is not installed (e.g. a CI
            # matrix that only runs the requests-based smoke tests). The
            # import only needs to succeed when this source is actually
            # used.
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            logger.warning(
                f"Playwright is not installed; KariyerNetPlaywrightSource "
                f"({self.name}) cannot render {self.search_url} and will "
                f"return []: {exc}"
            )
            return ""

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                try:
                    context = browser.new_context(
                        user_agent=_PLAYWRIGHT_USER_AGENT,
                        viewport={"width": 1366, "height": 900},
                        locale="tr-TR",
                    )
                    page = context.new_page()
                    # ``domcontentloaded`` + ``networkidle`` gives the
                    # Cloudflare interstitial time to run its JS and the
                    # job cards time to hydrate before we snapshot.
                    page.goto(
                        self.search_url,
                        wait_until="networkidle",
                        timeout=self.timeout,
                    )
                    html = page.content()
                finally:
                    # Always close the browser, even if navigation or
                    # content() raised, so we never leak a process.
                    browser.close()
        except PlaywrightError as exc:
            logger.warning(
                f"Playwright failed to render {self.search_url} for "
                f"{self.name}; returning []: {exc}"
            )
            return ""
        except Exception as exc:  # noqa: BLE001 — defensive top boundary
            logger.warning(
                f"Unexpected error rendering {self.search_url} for "
                f"{self.name} via Playwright; returning []: {exc}"
            )
            return ""

        if not html:
            logger.warning(
                f"Playwright returned empty HTML for {self.search_url} "
                f"({self.name}); returning []."
            )
        return html or ""


__all__ = ["KariyerNetPlaywrightSource"]
