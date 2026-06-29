"""Quick HTML probe for Peoplise / Hirex pages. Read-only — no parsing."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from app.sources.ats_helpers import DEFAULT_HEADERS  # noqa: E402

TARGETS = [
    ("peoplise_logo", "https://live.peoplise.com/logo/career"),
    ("hirex_papara", "https://app.gethirex.com/o/papara/"),
]


def main() -> int:
    for label, url in TARGETS:
        print(f"=== {label} :: {url} ===")
        try:
            response = requests.get(url, timeout=20, headers=DEFAULT_HEADERS)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue
        print(f"  status={response.status_code} bytes={len(response.text)}")
        # Show anchors that might be job links
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        anchors = soup.find_all("a", href=True)
        print(f"  total_anchors={len(anchors)}")
        sample: list[str] = []
        for a in anchors[:30]:
            href = str(a.get("href"))
            text = (a.get_text(" ", strip=True) or "")[:80]
            sample.append(f"    href={href!r} text={text!r}")
        for line in sample:
            print(line)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())