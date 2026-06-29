"""Live fetch probe for the new Sprint 1 sources.

Runs each new entry against its live URL with a short timeout and
prints the result. No persistence, no Telegram — read-only probe.

Usage::

    python scripts/probe_new_sources.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sources.hirex_source import HirexSource  # noqa: E402
from app.sources.hrpeak_source import HrPeakSource  # noqa: E402
from app.sources.lever_source import LeverSource  # noqa: E402
from app.sources.peoplise_source import PeopliseSource  # noqa: E402
from app.sources.zoho_recruit_source import ZohoRecruitSource  # noqa: E402


PROBES = [
    ("iyzico (lever)", lambda: LeverSource("iyzico", "iyzico")),
    ("Ziraat Teknoloji (hrpeak)", lambda: HrPeakSource("Ziraat Teknoloji", "https://ziraatteknoloji.hrpeak.com/jobs")),
    ("İnnova (hrpeak)", lambda: HrPeakSource("İnnova", "https://innova.hrpeak.com/jobs")),
    ("Logo Yazılım (peoplise)", lambda: PeopliseSource("Logo Yazılım", "https://live.peoplise.com/logo/career")),
    ("Papara (hirex)", lambda: HirexSource("Papara", "https://app.gethirex.com/o/papara/")),
    ("Param (zoho_recruit)", lambda: ZohoRecruitSource("Param", "https://param.zohorecruit.com/jobs/PARAM-Kariyer")),
]


def main() -> int:
    for label, factory in PROBES:
        print(f"=== {label} ===")
        try:
            source = factory()
            jobs = source.fetch_jobs()
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            print()
            continue
        print(f"  parsed={len(jobs)} job(s)")
        for job in jobs[:5]:
            print(f"    - {job.title[:80]} @ {job.company} | {job.url}")
        if len(jobs) > 5:
            print(f"    ... and {len(jobs) - 5} more")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())