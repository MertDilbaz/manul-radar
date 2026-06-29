"""Simulate V2 scoring on the 7 real production jobs Mert mentioned.

This is a one-off check (not a smoke test) used during the V2
scoring tuning session to confirm that the new tiered weights + confidence
tiers correctly rank the same jobs the old scorer was mis-ranking.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.config_loader import load_config
from app.models.job import Job
from app.models.scored_job import ScoredJob
from run_monitor import _build_scorer


def _make_job(title: str, company: str, source: str) -> Job:
    url_slug = company.lower().replace(" ", "-")
    return Job(
        title=title,
        company=company,
        location="Istanbul, Turkey",
        work_type=None,
        seniority=None,
        source=source,
        url=f"https://example.com/{url_slug}",
        description=(
            f"{title} role at {company} in Istanbul, Turkey. "
            "We are looking for a motivated engineer to join our team."
        ),
        published_at=None,
        discovered_at="2026-06-29T00:00:00",
    )


def main() -> int:
    config = load_config()
    scorer = _build_scorer(config)

    real_jobs = [
        ("AI Software Engineer", "Commencis", "lever_commencis"),
        ("Software Engineer", "Midas", "lever_midas"),
        ("Software Engineer, iOS", "Midas", "lever_midas"),
        ("AI Software Engineer - Remote", "Insider", "lever_insider"),
        ("Software Engineer", "iyzico", "lever_iyzico"),
        ("Junior Java Backend Developer", "Commencis", "lever_commencis"),
        ("Application Support Specialist (SQL)", "iyzico", "lever_iyzico"),
    ]

    print(
        f"scoring: minimum_score={scorer.minimum_score}, "
        f"strong_weight={scorer._strong_weight}, "
        f"weak_weight={scorer._weak_weight}, "
        f"location_weight={scorer._location_weight}, "
        f"company_boost_weight={scorer._company_boost_weight}, "
        f"mobile_penalty={scorer._mobile_penalty}, "
        f"generic_only_penalty={scorer._generic_only_penalty}"
    )
    print("=" * 100)
    for title, company, source in real_jobs:
        job = _make_job(title, company, source)
        scored: ScoredJob = scorer.score(job)
        icon = {
            "high": "🟢",
            "medium": "🟡",
            "low": "🔴",
        }.get(scored.confidence, "⚪")
        status = "RELEVANT" if scored.relevant else "rejected"
        excluded_preview = scored.excluded_keywords[:3]
        print(
            f"{icon} {status:10s} score={scored.score:4d} | "
            f"conf={scored.confidence or '-':6s} | "
            f"{title:42s} @ {company:14s} | excluded={excluded_preview}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())