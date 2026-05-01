#!/usr/bin/env python3
"""Update cached citation counts from a Google Scholar profile.

The homepage reads data/scholar-citations.json. This script is intended to run
from GitHub Actions on a low-frequency schedule so visitors never query Google
Scholar directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "scholar-citations.json"
SCHOLAR_PROFILE = "https://scholar.google.com/citations?user=iD5b5lUAAAAJ&hl=en"
SCHOLAR_LIST = SCHOLAR_PROFILE + "&cstart=0&pagesize=100&view_op=list_works&sortby=pubdate"

PAPERS = [
    {
        "key": "safetyfirst",
        "title": "CBF-Based Hierarchical Quadratic Programs With Guaranteed Feasibility for Safety-Critical Systems",
    },
    {
        "key": "idmb",
        "title": "Flexible and Safe Navigation of Autonomous Underwater Vehicles with Input-Dynamics Move Blocking",
    },
    {
        "key": "cac",
        "title": "Certificated Actor-Critic: Hierarchical Reinforcement Learning with Control Barrier Functions for Safe Navigation",
    },
]


def normalize_title(title: str) -> str:
    title = title.lower()
    title = title.replace("&amp;", "and")
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


class ScholarProfileParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[Dict[str, object]] = []
        self._row: Optional[Dict[str, List[str]]] = None
        self._in_title = False
        self._in_citations = False

    @staticmethod
    def _classes(attrs: Iterable[tuple[str, Optional[str]]]) -> set[str]:
        class_value = ""
        for name, value in attrs:
            if name == "class" and value:
                class_value = value
                break
        return set(class_value.split())

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        classes = self._classes(attrs)
        if tag == "tr" and "gsc_a_tr" in classes:
            self._row = {"title": [], "citations": []}
            return
        if self._row is None or tag != "a":
            return
        if "gsc_a_at" in classes:
            self._in_title = True
        elif "gsc_a_ac" in classes:
            self._in_citations = True

    def handle_data(self, data: str) -> None:
        if self._row is None:
            return
        if self._in_title:
            self._row["title"].append(data)
        elif self._in_citations:
            self._row["citations"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_title = False
            self._in_citations = False
        elif tag == "tr" and self._row is not None:
            title = " ".join(self._row["title"]).strip()
            citation_text = " ".join(self._row["citations"]).strip()
            citation_match = re.search(r"\d+", citation_text)
            if title:
                self.rows.append({
                    "title": re.sub(r"\s+", " ", title),
                    "count": int(citation_match.group(0)) if citation_match else 0,
                })
            self._row = None


def fetch_profile_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_profile(html: str) -> List[Dict[str, object]]:
    parser = ScholarProfileParser()
    parser.feed(html)
    return parser.rows


def load_existing(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"source": SCHOLAR_PROFILE, "updated_utc": None, "citations": {}}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_citation_data(rows: List[Dict[str, object]], existing: Dict[str, object]) -> Dict[str, object]:
    by_title = {normalize_title(str(row["title"])): row for row in rows}
    existing_citations = existing.get("citations")
    if not isinstance(existing_citations, dict):
        existing_citations = {}

    citations: Dict[str, Dict[str, object]] = {}
    matched_any = False
    for paper in PAPERS:
        key = paper["key"]
        title = paper["title"]
        row = by_title.get(normalize_title(title))
        if row is None:
            old_item = existing_citations.get(key)
            if isinstance(old_item, dict) and "count" in old_item:
                citations[key] = old_item
            continue

        matched_any = True
        citations[key] = {
            "title": title,
            "scholar_title": row["title"],
            "count": int(row["count"]),
        }

    if not matched_any:
        raise RuntimeError("No configured papers were matched on the Scholar profile.")

    return {
        "source": SCHOLAR_PROFILE,
        "updated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "citations": citations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=Path, help="Read Scholar HTML from a local file instead of the network.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    existing = load_existing(args.output)

    try:
        html = args.html.read_text(encoding="utf-8") if args.html else fetch_profile_html(SCHOLAR_LIST)
        if "Please show you're not a robot" in html:
            raise RuntimeError("Google Scholar returned a bot-check page.")
        rows = parse_profile(html)
        if not rows:
            raise RuntimeError("No publication rows were parsed from the Scholar profile.")
        updated = build_citation_data(rows, existing)
    except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
        print(f"Scholar citation update skipped: {error}", file=sys.stderr)
        return 0

    write_json(args.output, updated)
    print(f"Updated {args.output} with {len(updated['citations'])} citation counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
