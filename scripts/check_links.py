"""Check that every paper, code and project link in data/works.json answers.

    python scripts/check_links.py [--only ID ...]

For arXiv links it also checks that the abstract page's title matches the listed title,
which catches a wrong arXiv ID. Prints one line per problem; exit code 1 if there are any.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKS = ROOT / "data" / "works.json"
USER_AGENT = "Mozilla/5.0 (compatible; real2sim-map-linkcheck)"


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", html.unescape(text).lower())


def get(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(400_000).decode("utf-8", "replace") if "arxiv.org/abs" in url else ""
            return response.status, body
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return 0, str(error)


def check(work: dict) -> list[str]:
    problems = []
    for key in ("paper", "code", "project"):
        url = work.get(key)
        if not url:
            continue
        status, body = get(url)
        if status in (403, 429):  # some sites block scripts; not a broken link
            continue
        if status != 200:
            problems.append(f"{work['id']}: {key} {url} -> {status or body}")
            continue
        if "arxiv.org/abs" in url:
            match = re.search(r"<title>\s*\[[^\]]+\]\s*(.*?)</title>", body, re.S)
            if match and normalise(work["title"])[:40] not in normalise(match.group(1)):
                problems.append(f"{work['id']}: arXiv title is {match.group(1).strip()!r}, listed {work['title']!r}")
        time.sleep(0.3)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", default=None, help="check only these work ids")
    args = parser.parse_args()
    works = json.loads(WORKS.read_text(encoding="utf-8"))
    if args.only:
        works = [w for w in works if w["id"] in set(args.only)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        problems = [p for found in pool.map(check, works) for p in found]
    for problem in problems:
        print(problem)
    print(f"checked {len(works)} works, {len(problems)} problems", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
