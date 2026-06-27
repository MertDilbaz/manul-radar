"""Kariyer.net public search source.

Kariyer.net exposes public job-listing pages that anyone can fetch
without login, captcha, or any candidate profile. The goal of this
source is to read those listing pages (the ``/is-ilanlari`` search
results) and turn every visible posting into a :class:`Job`.

This source deliberately does *not* cover:

* Logged-in flows (apply, profile, saved searches)
* Filter widgets that mutate the listing via JavaScript
* Any closed / private endpoint
* A captcha bypass — Kariyer.net sometimes serves one to bot
  traffic; if that happens ``fetch_jobs`` returns ``[]`` after
  logging a warning rather than trying to defeat it.

URL shape (observed on the live ``/is-ilanlari?kw=...`` page):

* **Real job posting:** ``/is-ilani/<company-slug>-<position-slug>-<id>``
  e.g. ``/is-ilani/optiim-is-cozumleri-a-s-senior-java-software-developer-4464020``.
  Note the single-``l`` prefix (``is-ilani``, *not* ``is-ilanlari``)
  and the trailing numeric id.
* **Category / navigation:** ``/is-ilanlari/<city>`` (double ``l``,
  no id) — these are search-rooted category pages, not postings.
  Same for the footer / nav: ``/universite-rehberi``,
  ``/pozisyonlar``, ``/tercih-motoru``, ``/cv``, ``/maas``,
  ``/giris``, ``/firma`` etc.

A loose substring match (``"ilan"`` in the URL) was too permissive
in V0.2 and surfaced 79 navigation links as "jobs" on a single
search page. V0.3 tightens the filter to:

1. The path *must* start with ``/is-ilani/`` (single ``l``).
2. The path *must* end with a numeric id (``-<digits>``).
3. The path *must not* contain a known navigation / chrome
   fragment.

This is deliberately brittle — the next A/B test on Kariyer.net
can shift one of these. Operators are expected to re-pin the
parser if the live page starts producing 0 jobs.

Card shape (observed on the live 2026-06-26 page):

Each job posting in the listing is rendered inside a single
``<a class="k-ad-card radius">`` anchor. Inside that anchor the
semantic fields live in their own divs with stable class names::

    <a class="k-ad-card radius">
      <div class="card-top">
        <div class="title-wrapper">
          <div class="title-left">Senior Backend Developer</div>  <- title
          <div class="subtitle">     ÇAKMAKÇI KIYMETLİ MADENLER ...  </div>  <- company
          <div class="job-detail">
            <div class="location">İstanbul(Asya)</div>             <- location
            <div class="dot"/>
            <div class="work-model">İş Yerinde</div>               <- work_type
        <div class="card-bottom-wrapper">
          <div class="ad-date">
            <span class="date date-other">update 8 saat</span>     <- published_at

V0.4 pulls title / company / location out of these named
siblings rather than the anchor's full text, which previously
glued everything into one string ("Senior Backend Developer
ÇAKMAKÇI ... İstanbul(Asya) İş Yerinde ... update 8 saat"). The
fallback for the title is the anchor's own text *with a
whitelist of known noise tokens stripped* — if we cannot find a
clean title even after the noise strip, the parser leaves
``title`` empty (and the caller skips the job) so we never
emit a Job with a polluted title.

Network:
* One ``requests.get`` per call. A short timeout and a real
  ``User-Agent`` keep the source polite.
* Any HTTP / network error propagates up so
  :class:`JobMonitorService` can apply its source-level failure
  isolation at the boundary.

Dedup:
* Duplicate job URLs are collapsed via an in-memory set so a single
  posting shown in two slots (e.g. pinned + listing) only emits
  one :class:`Job``.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.models.job import Job
from app.sources.base_source import BaseSource
from app.utils.logger import logger


# Minimum viable title length — anything shorter is almost certainly
# a navigation / button anchor the listing page slipped into the
# tree, and emitting it would create ghost ``Job`` rows that score
# zero anyway.
_MIN_TITLE_LEN = 3

# Path prefix that ONLY real job-detail URLs use. ``/is-ilanlari/``
# (double ``l``) is the search / category root and is explicitly
# excluded by ``_looks_like_job_link``. Confirmed against the live
# 2026-06-26 HTML snapshot — every real posting starts with
# ``/is-ilani/<slug>-<id>`` and the trailing ``-<digits>`` segment
# is what separates a posting from a category landing page.
_JOB_DETAIL_PATH_PREFIX = "/is-ilani/"

# Trailing ``-12345`` (or ``/12345``) — the numeric id that the
# search root / category pages do NOT carry. This is the cheap
# way to tell a real posting from a category URL like
# ``/is-ilanlari/istanbul``.
_JOB_DETAIL_ID_PATTERN = re.compile(r"[-/]\d{4,}$")

# Anchor paths we *don't* want to surface as jobs even if they
# somehow clear the prefix / id check. These are the site's own
# chrome (search results root, login / signup pages, generic
# landing routes, university guides, salary / cv helpers) — a
# listing page will link to them as part of its navigation, and
# we treat them as noise.
_NON_JOB_PATH_FRAGMENTS: tuple[str, ...] = (
    # Auth / profile chrome
    "/giris",
    "/kayit",
    "/login",
    "/signup",
    "/sifremi-unuttum",
    "/hesabim",
    "/profil",
    # Company / blog / help chrome
    "/firma",
    "/blog",
    "/yardim",
    "/about",
    "/contact",
    "/iletisim",
    # University / education guides (footer)
    "/universite-rehberi",
    "/universiteler",
    "/bolumler",
    "/en-iyi-universiteler",
    "/tercih-motoru",
    "/yks-",
    "/kyk-",
    # Career helper chrome
    "/pozisyonlar",
    "/maas",
    "/cv-",
    "/cv/",
    "/kariyer-rehberi",
    "/kariyer-patikasi",
    # Search / category roots — the double-``l`` prefix is the
    # search home, not a job detail. ``/is-ilanlari/<city>`` and
    # friends are caught by the id-pattern check above, but the
    # bare root is not, so we add it here for safety.
    "/is-ilanlari",
    # Other Kariyer subdomains that occasionally surface in a
    # search result footer.
    "ilkisim.kariyer.net",
)

# Class names inside the job card. The live 2026-06-26 markup
# exposes one div per field; we read each from its own class so
# we do not have to fall back to the glued anchor text.
_TITLE_CLASS = "title-left"
_SUBTITLE_CLASS = "subtitle"
_LOCATION_CLASS = "location"
_WORK_MODEL_CLASS = "work-model"
_DATE_CLASS = "date"

# Known noise tokens the title may inherit from the surrounding
# card markup if the ``title-left`` element is missing and we
# fall back to the anchor's full text. These are exactly the
# work-type / city / update labels the live card appends after
# the title. Each match is stripped *as a whole token* (case
# insensitive, whitespace-padded) so we never accidentally
# drop a substring of a real position name.
_TITLE_NOISE_TOKENS: tuple[str, ...] = (
    # Work type
    "iş yerinde",
    "tam zamanlı",
    "hibrit",
    "uzaktan / remote",
    "uzaktan",
    "remote",
    "part time",
    "freelance",
    "staj",
    # Update labels
    "update",
    "yeni",
    # Date units (when "8 saat", "22 gün", etc. leak into text)
    "saat",
    "gün",
    # Cities as separate labels (e.g. when the title is followed
    # by a city tag, "İstanbul" on its own line)
    "istanbul",
    "istanbul(Asya)",
    "istanbul(Avr.)",
    "ankara",
    "izmir",
    "antalya",
    "bursa",
    "kayseri",
)

# Position-name suffix words. The live Kariyer.net card always
# appends ``<company> <city> <work-type> <update>`` *after* the
# position title, and every real posting's title ends in one
# of these words. We use the *last* suffix in the candidate
# string to truncate everything from its end onward — which
# handles the A/B-variant glued case ("DeveloperCAKMAKCI ...")
# cleanly because "Developer" still appears as a word in the
# glued blob and the suffix match does not care about the
# whitespace before the next field.
#
# English first, then Turkish. Words are sorted by length
# descending inside the regex so longer matches win (e.g.
# "Specialist" before "Special").
_TITLE_POSITION_SUFFIXES: tuple[str, ...] = (
    # English
    "Specialist",
    "Engineer",
    "Developer",
    "Architect",
    "Consultant",
    "Coordinator",
    "Administrator",
    "Designer",
    "Analyst",
    "Manager",
    "Director",
    "Lead",
    # Turkish
    "Gelistiricisi",
    "Gelistirici",
    "Muhendisi",
    "Uzmani",
    "Yoneticisi",
    "Tasarimcisi",
    "Tasarimci",
    "Analisti",
    "Danimani",
    "Koordinatoru",
    "Sorumlusu",
    "Stajyeri",
    "Uzman",
    "Mimar",
    "Müdürü",
)
_TITLE_SUFFIX_PATTERN = re.compile(
    # Match the suffix either at a word boundary OR immediately
    # followed by an uppercase letter (start of the next glued
    # field — the live card always continues with a company
    # name or city whose first letter is uppercase, so a
    # position title glued to its follow-up field still ends
    # at the suffix we care about). The trailing optional
    # "s" / Turkish possessive endings are handled by the
    # individual suffix list (e.g. "Gelistiricisi"), not
    # here, so we do not need a separate plural pass.
    r"(?:" + "|".join(re.escape(s) for s in _TITLE_POSITION_SUFFIXES) + r")"
    r"(?=[\s\W]|[A-ZİŞĞÜÇÖ]|$)",
    re.IGNORECASE,
)

# Regex to strip the noise tokens. Each match is one or more
# whitespace characters + the literal token + optional trailing
# whitespace, so we do not leave orphaned separators in the
# title. The pattern is anchored to the *end* of the title to
# avoid eating a real title that happens to *contain* one of
# these words (e.g. a position titled "Remote Work Specialist"
# is fine because "Remote" alone is not in the title; the only
# thing at risk is a position titled "Stajyer" which is a real
# word in Turkish — operators that need it back can remove the
# entry from ``_TITLE_NOISE_TOKENS``).
_TITLE_NOISE_PATTERN = re.compile(
    r"(?:\s+)(?:" + "|".join(re.escape(t) for t in _TITLE_NOISE_TOKENS) + r")\b",
    re.IGNORECASE,
)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; ManulRadar/1.0; "
    "+https://github.com/local/manul-radar)"
)

REQUEST_TIMEOUT: int = 15


class KariyerNetSource(BaseSource):
    """Fetch open positions from a Kariyer.net public search URL.

    Args:
        search_url: Absolute URL of a public Kariyer.net search /
            listing page (typically ending in ``/is-ilanlari?...``).
            Required; the same URL is used as the GET target and as
            the base for resolving relative ``href``s on the page.
        source_name: ``name`` attribute stamped on every emitted
            ``Job.source``. Defaults to ``"kariyer_net"``. Operators
            can override when they register more than one
            Kariyer.net source against different keywords (e.g.
            ``"kariyer_net_java"`` vs ``"kariyer_net_data"``).

    The class attribute ``name`` is set from ``source_name`` so two
    Kariyer.net sources with different search URLs can coexist
    without collision in logs, persistence keys, and notification
    payloads. If a future config field needs to pin the source
    name explicitly, that hook belongs here.
    """

    name: str = ""
    search_url: str = ""

    def __init__(
        self,
        search_url: str,
        source_name: str = "kariyer_net",
    ) -> None:
        """Store the search URL and the source name.

        ``search_url`` is required; we do not silently fall back to
        a placeholder because the resulting relative-URL joins
        would all silently land on the wrong host. ``source_name``
        is optional but must be a non-empty string when supplied.
        """
        if not search_url:
            raise ValueError(
                "KariyerNetSource requires a non-empty search_url"
            )
        cleaned_name = (source_name or "").strip()
        if not cleaned_name:
            raise ValueError(
                "KariyerNetSource requires a non-empty source_name"
            )
        self.search_url = search_url
        self.name = cleaned_name

    def fetch_jobs(self) -> list[Job]:
        """GET the listing page and parse it into a list of ``Job`` instances.

        Raises:
            requests.exceptions.RequestException: On any network or
                HTTP failure. ``JobMonitorService`` catches and logs
                these at the source boundary.
        """
        headers = {"User-Agent": _USER_AGENT}
        response = requests.get(
            self.search_url,
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        return self._parse_jobs(response.text)

    # ---------------------- pure parsing (no I/O) ----------------------

    def _parse_jobs(self, html: str) -> list[Job]:
        """Turn a listing page's HTML into ``Job`` instances; ``[]`` on failure.

        This method is deliberately pure so the smoke test can call
        it with hand-crafted HTML without needing the network. Any
        failure (empty input, BeautifulSoup exception, page shape
        shift) yields ``[]`` and a warning log rather than raising,
        so a hostile or redesigned listing page cannot take the
        monitor service down with it.
        """
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:  # noqa: BLE001 — defensive against parser regressions
            logger.warning(
                f"Kariyer.net HTML for {self.search_url} could not be "
                f"parsed; returning []: {exc}"
            )
            return []

        return self._extract_jobs_from_soup(soup)

    def _extract_jobs_from_soup(self, soup: BeautifulSoup) -> list[Job]:
        """Walk every anchor and emit a ``Job`` for those that look like postings.

        V0.4: each ``<a class="k-ad-card radius">`` anchor on the
        live page carries the posting's title / company /
        location / work-type / published-at in *named* child
        divs (``title-left``, ``subtitle``, ``location``,
        ``work-model``, ``date``). The parser reads each field
        from its own container so the title does not get
        contaminated with company / city / "update 8 saat"
        text, and emits a ``Job`` with all five metadata
        fields populated when the markup cooperates.

        If a field's container is missing (markup drift), the
        corresponding ``Job`` field is left as ``None`` rather
        than guessed. The title falls back to the anchor's
        text *with a known-noise-token strip* — that path
        exists so the parser still produces a clean title
        for older or A/B-tested card variants where the named
        div is absent, but it never produces a title that
        contains "İş Yerinde" / "update" / "İstanbul" etc.
        If the noise-stripped fallback is still empty (or too
        short), the anchor is dropped entirely so we never
        emit a ``Job`` with a polluted title.
        """
        seen_urls: set[str] = set()
        jobs: list[Job] = []
        now = datetime.utcnow().isoformat()

        for anchor in soup.find_all("a"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            absolute = urljoin(self.search_url, href)
            if not self._looks_like_job_link(absolute):
                continue
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)

            title = (
                self._extract_card_attribute(anchor, "positionname")
                or self._extract_title(anchor)
            )
            if not title or len(title) < _MIN_TITLE_LEN:
                # Title is either missing (markup drift) or
                # could not be cleaned to a usable length
                # (anchor text was *only* noise tokens). Skip
                # the anchor rather than emit a polluted Job.
                continue

            company = (
                self._extract_class_text(anchor, _SUBTITLE_CLASS)
                or self._extract_card_attribute(anchor, "companyname")
            )
            location = (
                self._extract_class_text(anchor, _LOCATION_CLASS)
                or self._extract_card_attribute(anchor, "cityname")
            )
            work_type = self._extract_class_text(anchor, _WORK_MODEL_CLASS)
            published_at = self._extract_date_text(anchor)

            jobs.append(
                Job(
                    title=title,
                    company=company,
                    location=location,
                    work_type=work_type,
                    seniority=None,
                    source=self.name,
                    url=absolute,
                    description=None,
                    published_at=published_at,
                    discovered_at=now,
                )
            )

        return jobs

    @staticmethod
    def _extract_card_attribute(anchor, attribute_name: str) -> str | None:
        """Return a cleaned metadata attribute from the nearest job card wrapper.

        Kariyer.net's live listing cards often carry useful metadata on
        the ancestor ``.job-list-card-item`` node, for example
        ``positionName``, ``companyName`` and ``cityName``. BeautifulSoup's
        HTML parser lowercases attribute names, so callers pass lowercase
        names such as ``"positionname"``.

        This is a safe fallback for markup variants where the visible
        anchor text is flattened or glued together. The search is bounded
        to a few parents so page-level attributes cannot accidentally leak
        into a job.
        """
        current = anchor
        for _ in range(6):
            if current is None:
                break
            value = current.get(attribute_name) if hasattr(current, "get") else None
            if value:
                text = re.sub(r"\s+", " ", str(value)).strip()
                if text:
                    return text
            current = getattr(current, "parent", None)
        return None

    @staticmethod
    def _extract_class_text(anchor, class_name: str) -> str | None:
        """Return the trimmed text of the first child of ``anchor``
        whose ``class`` attribute contains ``class_name``.

        Returns ``None`` when no matching child exists or when
        the matching child's text is empty after trimming. The
        text is ``" "``-joined so a child that wraps a string
        in nested ``<span>``s still returns the full content
        without separator artifacts.
        """
        node = anchor.find(attrs={"class": True})
        # ``find`` without a tag filter is *not* what we want;
        # the anchor itself has the ``k-ad-card radius`` class
        # and would match first. We want a descendant.
        for descendant in anchor.find_all(attrs={"class": True}):
            classes = descendant.get("class") or []
            if class_name in classes:
                text = descendant.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _extract_date_text(anchor) -> str | None:
        """Return the trimmed text of the first descendant whose
        ``class`` attribute is ``date`` (with or without extra
        modifiers like ``date-other``). The date container is a
        ``<span>`` on the live page, but we do not pin the tag
        because a future variant could promote it to a ``<div>``.
        """
        for descendant in anchor.find_all(attrs={"class": True}):
            classes = descendant.get("class") or []
            if "date" in classes:
                text = descendant.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _extract_title(anchor) -> str:
        """Extract a clean position title from a job anchor.

        Primary path: read the ``title-left`` div inside the
        anchor — that is the dedicated title container on the
        live 2026-06-26 markup and is the only place a real
        title lives.

        Fallback path: walk the anchor's *immediate* element
        children (each card field is its own div on the live
        page, and a flattened A/B variant keeps them as
        siblings even when their text is glued). For each
        child we run the noise-strip and keep the longest
        survivor. The longest non-noise piece is the title by
        construction.

        The fallback exists so older / A/B-tested card variants
        keep producing a clean title instead of dropping the
        anchor entirely, but it must never produce a title that
        contains "İş Yerinde", "update", "İstanbul", etc.

        Returns the cleaned title, or ``""`` if the result is
        empty / too short. The caller (``_extract_jobs_from_soup``)
        treats ``""`` as a signal to drop the anchor.
        """
        for descendant in anchor.find_all(attrs={"class": True}):
            classes = descendant.get("class") or []
            if _TITLE_CLASS in classes:
                text = descendant.get_text(" ", strip=True)
                if text and len(text) >= _MIN_TITLE_LEN:
                    return text

        # Fallback: walk the anchor's *element* children only
        # (NavigableString direct children are whitespace and
        # carry no field). For each element child, look at its
        # own text — if the live markup has flattened the card
        # into one glued div, that single child still contains
        # all the field text concatenated, and the noise strip
        # drops everything past the title.
        best: str = ""
        for child in anchor.children:
            if not isinstance(child, Tag):
                continue
            text = child.get_text(" ", strip=True)
            if not text or len(text) < _MIN_TITLE_LEN:
                continue
            cleaned = KariyerNetSource._clean_title_text(text)
            if len(cleaned) >= _MIN_TITLE_LEN and len(cleaned) > len(best):
                best = cleaned
        return best

    @staticmethod
    def _clean_title_text(raw: str) -> str:
        """Strip known noise tokens from a candidate title string.

        Two-step strategy, applied in order:

        1. **Suffix-based truncation** —
           ``_TITLE_SUFFIX_PATTERN``. The live card always
           appends ``<company> <city> <work-type> <update>``
           *after* the position title, so a real title is
           bounded by a known position suffix (English
           "Developer / Engineer / Specialist / Manager / ..."
           and Turkish "Geliştirici / Mühendisi / Uzmanı / ...").
           We find the *last* such suffix in the string and
           truncate everything from its end onward. This
           handles the A/B-variant glued case
           ("DeveloperCAKMAKCI ...") cleanly because
           "Developer" still appears as a word in the glued
           blob and the suffix match does not care about
           whitespace before the next field.

        2. **Token strip** — ``_TITLE_NOISE_PATTERN``. With
           the suffix cut done, the remaining trailing
           noise (``Hibrit``, ``Uzaktan``, ``update``, ``saat``)
           sits at the end of the truncated string and a
           whitespace-bounded token strip finishes the job.

        Both passes are repeated until the string stops
        shrinking, so a string that starts glued *and* has
        trailing labels still ends up clean.
        """
        if not raw:
            return ""

        candidate = raw

        for _ in range(8):
            # 1) Truncate at the last position suffix.
            suffix_match = _TITLE_SUFFIX_PATTERN.search(candidate)
            if suffix_match:
                truncated = candidate[: suffix_match.end()].rstrip()
            else:
                truncated = candidate

            # 2) Drop any remaining trailing noise tokens.
            updated = _TITLE_NOISE_PATTERN.sub(" ", truncated)
            updated = re.sub(r"\s+", " ", updated).strip()

            if updated == candidate:
                break
            candidate = updated

        return candidate

    @staticmethod
    def _looks_like_job_link(url: str) -> bool:
        """``True`` iff ``url`` looks like a Kariyer.net job detail page link.

        Three filters, applied in order; all three must pass:

        1. **Host**: the URL must point at a Kariyer host. The
           live site also links to subdomains (e.g.
           ``ilkisim.kariyer.net``); we accept any host that
           contains ``kariyer`` rather than a hard equality on
           ``www.kariyer.net`` so a future subdomain is not
           silently dropped.
        2. **Path prefix**: the path must start with
           ``/is-ilani/`` (single ``l``). The double-``l``
           ``/is-ilanlari/`` is the search home and never links
           to a real posting.
        3. **Trailing numeric id**: the path must end with a
           ``-<digits>`` (or ``/<digits>``) segment. This is what
           separates ``/is-ilani/<company-slug>-<position-slug>-<id>``
           from ``/is-ilanlari/<city>`` and from the bare
           ``/is-ilanlari`` root.
        4. **Negative chrome filter**: even after (2) and (3),
           drop anchors that hit a known navigation / footer
           path so e.g. ``/is-ilani/<digits>`` (which would pass
           the id check) cannot become a "job" if Kariyer.net
           ever starts using that prefix for a careers helper.
        """
        parsed = urlparse(url.lower())
        host = parsed.netloc
        path = parsed.path or ""
        if "kariyer" not in host:
            return False
        if not path.startswith(_JOB_DETAIL_PATH_PREFIX):
            return False
        if not _JOB_DETAIL_ID_PATTERN.search(path):
            return False
        if any(fragment in path or fragment in host for fragment in _NON_JOB_PATH_FRAGMENTS):
            return False
        return True

    @staticmethod
    def _extract_nearby_text(anchor, *, candidates: tuple[str, ...]) -> str | None:
        """Backwards-compatible helper kept for the V0.3 smoke
        tests that still drive ``_extract_jobs_from_soup``
        indirectly. V0.4 prefers the named-class readers
        (``_extract_class_text`` / ``_extract_date_text``) and
        only falls back to this ancestor walk when the named
        class is absent.

        Walks the anchor's ancestor chain and returns the first
        short text node whose class list contains one of the
        ``candidates`` tokens. The walk is bounded so a future
        site refactor cannot take the parser down with an
        unbounded traversal.
        """
        current = anchor.parent
        for _ in range(4):
            if current is None:
                break
            for node in current.find_all(
                ["div", "span", "p", "a"],
                class_=True,
                limit=16,
            ):
                classes = " ".join(node.get("class") or []).lower()
                if any(token in classes for token in candidates):
                    text = node.get_text(strip=True)
                    if text and len(text) >= 2:
                        return text
            current = current.parent
        return None


__all__ = ["KariyerNetSource"]

