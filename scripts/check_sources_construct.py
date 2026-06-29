"""Sanity check that ``_build_sources_from_config`` can construct every
real ``config.sources`` + ``companies.yaml`` source without raising.

Run with ``python scripts/check_sources_construct.py`` from the project
root.

This is the production-shape smoke check that complements
``tests/smoke_source_name_contract.py``: the contract test instantiates
each parser with hand-crafted args; this script instantiates every
*real* source from the live config dict so a regression that drops a
required kwarg, mistypes a parser name, or forgets a config field is
caught locally before CI does.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unittest import mock

from app.config.config_loader import load_config, load_optional_config
from run_monitor import _build_sources_from_config


def main() -> int:
    config = load_config()
    companies_cfg = load_optional_config()
    sources_cfg = config.get("sources") or []
    companies = companies_cfg.get("companies") or []
    print(f"config.sources entries: {len(sources_cfg)}")
    print(f"companies.yaml entries: {len(companies)}")

    with mock.patch("run_monitor.load_optional_config", return_value=companies_cfg):
        try:
            sources = _build_sources_from_config(config)
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 1
    print(f"Built {len(sources)} sources OK:")
    for source in sources:
        cls = type(source).__name__
        print(f"  - {cls:30s} name={source.name!r}")
    print(f"\nSOURCES_CONSTRUCT_OK count={len(sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())