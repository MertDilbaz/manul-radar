"""Smoke test pinning the V0.3 source-constructor contract.

Run with ``python tests/smoke_source_name_contract.py`` from the project
root. Prints ``<NAME>_OK ...`` lines on success and exits 0. On any
failure prints ``<NAME>_FAIL ...`` and exits 1.

The test enforces a contract that :func:`run_monitor._build_sources_from_config`
relies on: **every** parser constructor accepts an optional ``source_name``
keyword argument and uses it to override the derived ``name`` attribute.

The 2026-06-29 production incident (GitHub Actions #13) was caused by
``SuccessFactorsSource.__init__`` not accepting ``source_name`` even
though the dispatch helper was passing it. This test would have caught
it before commit, and re-running it after every refactor guarantees
the caller / callee contract stays in sync.
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


def _check_construct_with_source_name(parser_name: str, factory) -> None:
    """Build ``factory(source_name="<custom>")`` and assert name override.

    Each parser accepts a different set of required kwargs (e.g.
    LeverSource needs ``company_slug``, GreenhouseSource needs
    ``board_token``). The factory wraps the per-parser kwargs so a
    regression in any one of them produces a single failure pointing
    at the right parser.
    """
    try:
        source = factory(source_name="custom_name_override")
    except TypeError as exc:
        _record(
            f"{parser_name}_ACCEPTS_SOURCE_NAME",
            False,
            f"TypeError: {exc}",
        )
        return
    _record(
        f"{parser_name}_ACCEPTS_SOURCE_NAME",
        source.name == "custom_name_override",
        f"name={source.name!r}",
    )


def _check_construct_without_source_name(parser_name: str, factory) -> None:
    """The default (no source_name) path must still derive a non-empty name.

    A refactor that *replaces* the derived name with an empty string
    whenever ``source_name`` is not supplied would silently lose source
    ids in production. This check pins the fallback behaviour.
    """
    source = factory()
    _record(
        f"{parser_name}_DEFAULT_NAME_NON_EMPTY",
        bool(source.name),
        f"name={source.name!r}",
    )


def main() -> int:
    from app.sources.hrpeak_source import HrPeakSource
    from app.sources.successfactors_source import SuccessFactorsSource
    from app.sources.workable_source import WorkableSource
    from app.sources.greenhouse_source import GreenhouseSource
    from app.sources.lever_source import LeverSource
    from app.sources.smartrecruiters_source import SmartRecruitersSource
    from app.sources.teamtailor_source import TeamtailorSource
    from app.sources.kariyer_net_source import KariyerNetSource
    from app.sources.peoplise_source import PeopliseSource
    from app.sources.hirex_source import HirexSource
    from app.sources.zoho_recruit_source import ZohoRecruitSource

    factories = [
        (
            "HRPEAK",
            lambda source_name=None: HrPeakSource(
                company_name="Acme",
                careers_url="https://acme.hrpeak.com/ilan/site.aspx",
                source_name=source_name,
            ),
        ),
        (
            "SUCCESSFACTORS",
            lambda source_name=None: SuccessFactorsSource(
                company_name="Acme",
                careers_url="https://acme.successfactors.com/careers",
                source_name=source_name,
            ),
        ),
        (
            "WORKABLE",
            lambda source_name=None: WorkableSource(
                company_name="Acme",
                account="acme",
                source_name=source_name,
            ),
        ),
        (
            "GREENHOUSE",
            lambda source_name=None: GreenhouseSource(
                company_name="Acme",
                board_token="acme",
                source_name=source_name,
            ),
        ),
        (
            "LEVER",
            lambda source_name=None: LeverSource(
                company_name="Acme",
                company_slug="acme",
                source_name=source_name,
            ),
        ),
        (
            "SMARTRECRUITERS",
            lambda source_name=None: SmartRecruitersSource(
                company_name="Acme",
                company_slug="Acme",
                source_name=source_name,
            ),
        ),
        (
            "TEAMTAILOR",
            lambda source_name=None: TeamtailorSource(
                company_name="Acme",
                careers_url="https://acme.teamtailor.com/jobs",
                source_name=source_name,
            ),
        ),
        (
            "KARIYER_NET",
            lambda source_name=None: KariyerNetSource(
                search_url="https://www.kariyer.net/is-ilanlari?kw=java",
                source_name=source_name or "kariyer_net_test",
            ),
        ),
        (
            "PEOPLISE",
            lambda source_name=None: PeopliseSource(
                company_name="Acme",
                careers_url="https://live.peoplise.com/acme/career",
                source_name=source_name,
            ),
        ),
        (
            "HIREX",
            lambda source_name=None: HirexSource(
                company_name="Acme",
                careers_url="https://app.gethirex.com/o/acme/",
                source_name=source_name,
            ),
        ),
        (
            "ZOHO_RECRUIT",
            lambda source_name=None: ZohoRecruitSource(
                company_name="Acme",
                careers_url="https://acme.zohorecruit.com/jobs/Portal",
                source_name=source_name,
            ),
        ),
    ]

    for parser_name, factory in factories:
        _check_construct_with_source_name(parser_name, factory)
        _check_construct_without_source_name(parser_name, factory)

    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL_SOURCE_NAME_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())