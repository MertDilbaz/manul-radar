"""Find job-detail anchor pattern in Zoho Recruit HTML."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from app.sources.ats_helpers import DEFAULT_HEADERS  # noqa: E402

URL = "https://param.zohorecruit.com/jobs/PARAM-Kariyer"


def main() -> int:
    response = requests.get(URL, timeout=30, headers=DEFAULT_HEADERS)
    print(f"status={response.status_code} bytes={len(response.text)}")

    # Find all anchor href values, look for job-shaped URLs.
    pattern = re.compile(r'href="([^"]+)"', re.IGNORECASE)
    candidates: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(response.text):
        href = match.group(1)
        if href in seen:
            continue
        seen.add(href)
        # Only keep candidates that look job-related.
        lowered = href.lower()
        if any(token in lowered for token in (
            "/job", "viewjob", "jobposting", "recruit/job", "career",
            "kariyer", "ilan",
        )):
            candidates.append(href)

    print(f"\nFound {len(candidates)} candidate job anchors:")
    for href in candidates[:30]:
        print(f"  {href}")

    # Also probe common Zoho Recruit endpoints with no extra params.
    endpoints = [
        "https://param.zohorecruit.com/recruit/v2/PublishedJobPostings",
        "https://param.zohorecruit.com/recruit/v2/PublishedJobPostings?portalId=paramkariyer",
        "https://param.zohorecruit.com/recruit/v2/PublishedJobPostings?portalLink=PARAM-Kariyer",
    ]
    for url in endpoints:
        try:
            r = requests.get(url, timeout=20, headers=DEFAULT_HEADERS)
            print(f"\n{url}\n  status={r.status_code} head={r.text[:200]!r}")
        except Exception as exc:
            print(f"\n{url}\n  ERROR: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())