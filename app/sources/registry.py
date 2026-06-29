"""Parser registry — maps ``parser`` config keys to source factory functions.

Before this module existed, ``run_monitor._build_sources_from_config``
contained a 10-branch ``if parser == "hrpeak" / elif parser == ...``
chain. Every new parser required editing that function, violating the
Open/Closed Principle. This registry replaces the chain with a simple
dictionary lookup: adding a new parser is now a matter of adding one
entry to ``PARSER_REGISTRY`` (or calling :func:`register_parser`).

Each factory receives the raw config ``entry`` dict and the pre-parsed
common fields (``company``, ``name``, ``url``) so it can pull parser-
specific keys (``board_token``, ``company_slug``, ``account``, …)
without repeating the boilerplate extraction.
"""
from __future__ import annotations

from app.sources.base_source import BaseSource
from app.sources.dummy_source import DummySource
from app.sources.greenhouse_source import GreenhouseSource
from app.sources.hirex_source import HirexSource
from app.sources.hrpeak_source import HrPeakSource
from app.sources.kariyer_net_source import KariyerNetSource
from app.sources.lever_source import LeverSource
from app.sources.peoplise_source import PeopliseSource
from app.sources.smartrecruiters_source import SmartRecruitersSource
from app.sources.successfactors_source import SuccessFactorsSource
from app.sources.teamtailor_source import TeamtailorSource
from app.sources.workable_source import WorkableSource
from app.sources.zoho_recruit_source import ZohoRecruitSource
from app.utils.logger import logger


def _build_hrpeak(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("hrpeak requires company and url")
    return HrPeakSource(
        company_name=company,
        careers_url=url,
        source_name=name or None,
    )


def _build_successfactors(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("successfactors requires company and url")
    return SuccessFactorsSource(
        company_name=company,
        careers_url=url,
        source_name=name or None,
    )


def _build_workable(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    account = str(entry.get("account") or entry.get("slug") or "").strip()
    if not company or not (account or url):
        raise ValueError("workable requires company and account or url")
    return WorkableSource(
        company_name=company,
        account=account or None,
        careers_url=url or None,
        source_name=name or None,
    )


def _build_greenhouse(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    board_token = str(entry.get("board_token") or entry.get("token") or entry.get("slug") or "").strip()
    if not company or not board_token:
        raise ValueError("greenhouse requires company and board_token")
    return GreenhouseSource(
        company_name=company,
        board_token=board_token,
        source_name=name or None,
    )


def _build_lever(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    company_slug = str(entry.get("company_slug") or entry.get("slug") or "").strip()
    if not company or not company_slug:
        raise ValueError("lever requires company and company_slug")
    return LeverSource(
        company_name=company,
        company_slug=company_slug,
        source_name=name or None,
    )


def _build_smartrecruiters(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    company_slug = str(entry.get("company_slug") or entry.get("slug") or "").strip()
    if not company or not company_slug:
        raise ValueError("smartrecruiters requires company and company_slug")
    return SmartRecruitersSource(
        company_name=company,
        company_slug=company_slug,
        careers_url=url or None,
        source_name=name or None,
    )


def _build_teamtailor(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("teamtailor requires company and url")
    return TeamtailorSource(
        company_name=company,
        careers_url=url,
        source_name=name or None,
    )


def _build_kariyer_net(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not url:
        raise ValueError("kariyer_net requires url")
    return KariyerNetSource(
        search_url=url,
        source_name=name or "kariyer_net",
    )


def _build_peoplise(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("peoplise requires company and url")
    account = str(entry.get("account") or "").strip()
    return PeopliseSource(
        company_name=company,
        careers_url=url,
        account=account or None,
    )


def _build_hirex(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("hirex requires company and url")
    slug = str(entry.get("account") or entry.get("slug") or "").strip()
    return HirexSource(
        company_name=company,
        careers_url=url,
        slug=slug or None,
    )


def _build_zoho_recruit(entry: dict, *, company: str, name: str, url: str) -> BaseSource:
    if not company or not url:
        raise ValueError("zoho_recruit requires company and url")
    portal_name = str(
        entry.get("portal_name") or entry.get("account") or ""
    ).strip()
    return ZohoRecruitSource(
        company_name=company,
        careers_url=url,
        portal_name=portal_name or None,
    )


#: Maps ``parser`` config key to ``(factory_fn, display_suffix)``.
#: The ``display_suffix`` is used in the "Registered source" log line
#: so the operator sees a meaningful identifier per source.
PARSER_REGISTRY: dict[str, tuple] = {
    "hrpeak": (_build_hrpeak, "url"),
    "successfactors": (_build_successfactors, "url"),
    "workable": (_build_workable, "url_or_account"),
    "greenhouse": (_build_greenhouse, "board_token"),
    "lever": (_build_lever, "company_slug"),
    "smartrecruiters": (_build_smartrecruiters, "url_or_company_slug"),
    "teamtailor": (_build_teamtailor, "url"),
    "kariyer_net": (_build_kariyer_net, "name_and_url"),
    "peoplise": (_build_peoplise, "url"),
    "hirex": (_build_hirex, "url"),
    "zoho_recruit": (_build_zoho_recruit, "url"),
}


def _display_suffix(parser: str, display_key: str, entry: dict, company: str, url: str) -> str:
    """Return the value to show after ``parser / company ->`` in the log."""
    if display_key == "board_token":
        return str(entry.get("board_token") or entry.get("token") or entry.get("slug") or "").strip()
    if display_key in ("company_slug", "url_or_company_slug"):
        slug = str(entry.get("company_slug") or entry.get("slug") or "").strip()
        return url or slug if display_key == "url_or_company_slug" else slug
    if display_key == "url_or_account":
        account = str(entry.get("account") or entry.get("slug") or "").strip()
        return url or account
    if display_key == "name_and_url":
        return url
    return url


def build_source_from_entry(
    entry: dict,
    *,
    index: int,
) -> BaseSource | None:
    """Build one ``BaseSource`` from a config entry dict.

    Returns ``None`` if the entry is disabled or has no ``parser`` key.
    Raises ``ValueError`` if required fields are missing — the caller
    is expected to catch and log it, matching the pre-refactor behavior.
    """
    if not entry.get("enabled", True):
        logger.info(f"sources[{index}] disabled by config; skipping.")
        return None

    parser = str(entry.get("parser") or "").strip().lower()
    if not parser:
        logger.warning(f"sources[{index}] has no 'parser' key; skipping.")
        return None

    entry_in = PARSER_REGISTRY.get(parser)
    if entry_in is None:
        supported = ", ".join(sorted(PARSER_REGISTRY))
        logger.warning(
            f"Unknown parser {parser!r} at sources[{index}]; skipping. "
            f"Supported: {supported}."
        )
        return None

    factory_fn, display_key = entry_in
    company = str(entry.get("company") or "").strip()
    name = str(entry.get("name") or "").strip()
    url = str(entry.get("url") or entry.get("careers_url") or "").strip()

    source = factory_fn(entry, company=company, name=name, url=url)

    display = _display_suffix(parser, display_key, entry, company, url)
    if parser == "kariyer_net":
        logger.info(
            f"Registered source: kariyer_net (name={name or 'kariyer_net'}) -> {url}"
        )
    else:
        logger.info(f"Registered source: {parser} / {company} -> {display}")

    return source


__all__ = [
    "PARSER_REGISTRY",
    "build_source_from_entry",
]
