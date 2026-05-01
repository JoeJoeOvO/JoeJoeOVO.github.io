#!/usr/bin/env python3
"""Update cached GitHub star counts and Bilibili video views."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "resource-metrics.json"

RESOURCES = {
    "transsafe-video": {
        "kind": "bilibili",
        "bvid": "BV1DcPvzVEoT",
        "metric": "views",
    },
    "transsafe-code": {
        "kind": "github",
        "repo": "JoeJoeOvO/Transfer-Your-Safety",
        "metric": "stars",
    },
    "safetyfirst-video": {
        "kind": "bilibili",
        "bvid": "BV1yk2MBUE13",
        "metric": "views",
    },
    "safetyfirst-code": {
        "kind": "github",
        "repo": "JoeJoeOvO/Safety-first",
        "metric": "stars",
    },
    "idmb-code": {
        "kind": "github",
        "repo": "CLASS-Lab/BlueROV2Heavy-Gazebo-with-IDMB-CBF-DOB",
        "metric": "stars",
    },
    "cac-video": {
        "kind": "bilibili",
        "bvid": "BV1aTFLeWEki",
        "metric": "views",
    },
    "cac-code": {
        "kind": "github",
        "repo": "JoeJoeOvO/Certificated-Actor-Critic",
        "metric": "stars",
    },
}


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def load_existing(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"updated_utc": None, "metrics": {}}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def format_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if count >= 1_000:
        return f"{count / 1_000:.1f}k".rstrip("0").rstrip(".")
    return str(count)


def format_metric(count: int, metric: str) -> str:
    if metric == "stars":
        return f"{format_count(count)} {'star' if count == 1 else 'stars'}"
    if metric == "views":
        return f"{format_count(count)} views"
    return format_count(count)


def github_stars(repo: str) -> int:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JoeJoeOVO-homepage-metrics",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = fetch_json(f"https://api.github.com/repos/{quote(repo, safe='/')}", headers)
    return int(data["stargazers_count"])


def bilibili_views(bvid: str) -> int:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    }
    data = fetch_json(f"https://api.bilibili.com/x/web-interface/view?bvid={quote(bvid)}", headers)
    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"Bilibili returned code {data.get('code')} for {bvid}")
    return int(data["data"]["stat"]["view"])


def update_metrics(existing: Dict[str, object]) -> Dict[str, object]:
    existing_metrics = existing.get("metrics")
    if not isinstance(existing_metrics, dict):
        existing_metrics = {}

    metrics: Dict[str, Dict[str, object]] = {}
    updated_any = False

    for key, resource in RESOURCES.items():
        try:
            if resource["kind"] == "github":
                count = github_stars(str(resource["repo"]))
            elif resource["kind"] == "bilibili":
                count = bilibili_views(str(resource["bvid"]))
            else:
                raise RuntimeError(f"Unknown resource kind: {resource['kind']}")
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError, RuntimeError) as error:
            old_item = existing_metrics.get(key)
            if isinstance(old_item, dict) and "count" in old_item:
                metrics[key] = old_item
            print(f"Resource metric skipped for {key}: {error}", file=sys.stderr)
            continue

        updated_any = True
        metrics[key] = {
            "kind": resource["kind"],
            "metric": resource["metric"],
            "count": count,
            "display": format_metric(count, str(resource["metric"])),
        }
        if resource["kind"] == "github":
            metrics[key]["repo"] = resource["repo"]
        if resource["kind"] == "bilibili":
            metrics[key]["bvid"] = resource["bvid"]

    if not updated_any and not metrics:
        raise RuntimeError("No resource metrics were updated or preserved.")

    return {
        "updated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
    }


def main() -> int:
    existing = load_existing(OUTPUT_PATH)
    try:
        updated = update_metrics(existing)
    except RuntimeError as error:
        print(f"Resource metrics update skipped: {error}", file=sys.stderr)
        return 0

    write_json(OUTPUT_PATH, updated)
    print(f"Updated {OUTPUT_PATH} with {len(updated['metrics'])} resource metrics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
