#!/usr/bin/env python3
"""Fetch and publish metric caches without changing the user's checkout or index."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import update_resource_metrics as resources
import update_scholar_citations as scholar


ROOT = Path(__file__).resolve().parents[1]
CACHE_GROUPS = {
    "data/scholar-citations.json": "citations",
    "data/resource-metrics.json": "metrics",
}


def timestamp(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merge_cache(current, fetched, group):
    """Only replace entries with a newer successful observation, including decreases."""
    merged = copy.deepcopy(current)
    entries = merged.setdefault(group, {})
    accepted = []
    for key, item in fetched.get(group, {}).items():
        observed = item.get("updated_utc")
        previous = entries.get(key, {})
        if observed and timestamp(observed) > timestamp(previous.get("updated_utc")):
            entries[key] = copy.deepcopy(item)
            accepted.append(observed)
    if accepted:
        latest = max(accepted, key=timestamp)
        if timestamp(latest) >= timestamp(current.get("updated_utc")):
            merged["updated_utc"] = latest
            for key in ("source", "source_url"):
                if key in fetched:
                    merged[key] = fetched[key]
    return merged


class Git:
    def __init__(self, repo, proxy=""):
        self.repo = Path(repo)
        self.options = ["-c", "credential.interactive=never"]
        if proxy:
            self.options += [
                "-c", f"http.https://github.com.proxy={proxy}",
                "-c", "http.version=HTTP/1.1",
            ]
        self.fetch_ref = "refs/homepage-metrics/" + uuid.uuid4().hex

    def run(self, *args, input=None, env=None):
        process_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        process_env.update(env or {})
        result = subprocess.run(
            ["git", "-C", str(self.repo), *self.options, *args],
            input=input.encode("utf-8") if input is not None else None,
            capture_output=True, env=process_env, timeout=120,
        )
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git {args[0]} failed: {error}")
        return result.stdout.decode("utf-8", errors="replace").strip()

    def fetch(self):
        # A private ref avoids races with the user's fetch/pull and FETCH_HEAD.
        self.run("fetch", "--no-tags", "--no-write-fetch-head", "origin",
                 f"refs/heads/master:{self.fetch_ref}")
        return self.run("rev-parse", self.fetch_ref)

    def cache(self, revision, path):
        return json.loads(self.run("show", f"{revision}:{path}"))

    def close(self):
        self.run("update-ref", "-d", self.fetch_ref)


def publish(git, candidates, message, attempts=3, retry_delay=5):
    for attempt in range(attempts):
        try:
            parent = git.fetch()
            changes = {}
            for path, fetched in candidates.items():
                current = git.cache(parent, path)
                merged = merge_cache(current, fetched, CACHE_GROUPS[path])
                if merged != current:
                    changes[path] = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
            if not changes:
                print("Remote caches already contain these observations or newer data.")
                return parent

            # Build a commit on the latest remote tree using an isolated index.
            # No checkout, stash, reset, merge, rebase, or force push is needed.
            with tempfile.TemporaryDirectory(prefix="homepage-metrics-index-") as directory:
                index_env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
                git.run("read-tree", parent, env=index_env)
                for path, content in changes.items():
                    blob = git.run("hash-object", "-w", "--stdin", input=content)
                    git.run("update-index", "--add", "--cacheinfo",
                            f"100644,{blob},{path}", env=index_env)
                tree = git.run("write-tree", env=index_env)
                commit = git.run(
                    "-c", "user.name=github-actions[bot]",
                    "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit-tree", tree, "-p", parent, "-m", message,
                )
            git.run("push", "origin", f"{commit}:refs/heads/master")
            print(f"Published {', '.join(changes)} to master ({commit[:7]}).")
            return commit
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            if attempt + 1 == attempts:
                raise
            print(f"Publish attempt {attempt + 1} failed; refreshing remote data: {error}")
            time.sleep(retry_delay)
    raise RuntimeError("Metric publication did not complete.")


def collect(existing, resources_only=False):
    candidates = {}
    failures = []
    if not resources_only:
        try:
            rows, source_url = scholar.fetch_profile_rows(retries=3, retry_delay=5)
            path = "data/scholar-citations.json"
            candidates[path] = scholar.build_citation_data(rows, existing[path], source_url)
            for key, item in candidates[path]["citations"].items():
                print(f"Scholar {key}: {item['count']} citations; last success {item.get('updated_utc', 'unknown')}")
        except Exception as error:
            print(f"Scholar failed; existing citation counts and timestamps kept: {error}")
            failures.append("Scholar")
    try:
        path = "data/resource-metrics.json"
        candidates[path] = resources.update_metrics(existing[path])
        if candidates[path] == existing[path]:
            failures.append("all resource fetches")
    except Exception as error:
        print(f"Resource fetch failed; existing counts and timestamps kept: {error}")
        failures.append("resources")
    return candidates, failures


class Tee:
    def __init__(self, stream, logfile):
        self.stream = stream
        self.logfile = logfile

    def write(self, text):
        self.stream.write(text)
        self.logfile.write(text)
        self.flush()

    def flush(self):
        self.stream.flush()
        self.logfile.flush()


def run(args):
    if args.proxy:
        os.environ["SCHOLAR_PROXY"] = args.proxy
    if args.use_browser:
        os.environ["SCHOLAR_BROWSER_FETCH"] = "1"
    git = Git(ROOT, args.proxy)
    try:
        head = git.fetch()
        existing = {path: git.cache(head, path) for path in CACHE_GROUPS}
        candidates, failures = collect(existing, args.resources_only)
        if candidates:
            publish(git, candidates, args.commit_message)
        if failures:
            print("Update incomplete: " + ", ".join(failures) + ". The scheduled task may retry.")
            return 1
        print("Homepage metrics updated successfully. Your checkout and staged edits were not changed.")
        return 0
    finally:
        git.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources-only", action="store_true", help="Never contact Google Scholar (for GitHub Actions).")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--use-browser", action="store_true")
    parser.add_argument("--commit-message", default="Update homepage metric caches")
    args = parser.parse_args()
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"homepage-metrics-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
    print(f"Update log: {log_path}")
    with log_path.open("w", encoding="utf-8") as logfile:
        with redirect_stdout(Tee(sys.stdout, logfile)), redirect_stderr(Tee(sys.stderr, logfile)):
            print(f"Started at {datetime.now().astimezone().isoformat(timespec='seconds')}")
            try:
                return run(args)
            except Exception as error:
                print(f"Homepage metrics update failed: {error}", file=sys.stderr)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
