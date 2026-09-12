import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_homepage_metrics as updater


OLD = "2026-09-01T12:00:00Z"
NEW = "2026-09-12T12:00:00Z"
NEWER = "2026-09-12T13:00:00Z"
RESOURCE_PATH = "data/resource-metrics.json"
SCHOLAR_PATH = "data/scholar-citations.json"


def cache(time=OLD, video=10, stars=5):
    return {"updated_utc": time, "metrics": {
        "video": {"count": video, "updated_utc": time},
        "stars": {"count": stars, "updated_utc": time},
    }}


class CacheTests(unittest.TestCase):
    def test_merge_keeps_newer_remote_entry_and_accepts_count_decrease(self):
        remote = cache(NEWER, video=30, stars=5)
        remote["metrics"]["stars"]["updated_utc"] = OLD
        fetched = cache(NEW, video=20, stars=4)
        merged = updater.merge_cache(remote, fetched, "metrics")
        self.assertEqual(merged["metrics"]["video"]["count"], 30)
        self.assertEqual(merged["metrics"]["stars"]["count"], 4)
        self.assertEqual(merged["updated_utc"], NEWER)
        self.assertEqual(remote["metrics"]["stars"]["count"], 5)

    def test_global_timestamp_does_not_make_failed_legacy_entries_fresh(self):
        remote = cache()
        fetched = {"updated_utc": NEW, "metrics": {"video": {"count": 99}}}
        self.assertEqual(updater.merge_cache(remote, fetched, "metrics"), remote)

    def test_all_resources_fail_preserves_entire_cache(self):
        existing = cache()
        with patch.object(updater.resources, "github_stars", side_effect=URLError("offline")), \
             patch.object(updater.resources, "bilibili_views", side_effect=URLError("offline")):
            result = updater.resources.update_metrics(existing)
        self.assertEqual(result, existing)

    def test_partial_resource_failure_preserves_that_items_timestamp(self):
        existing = {"updated_utc": OLD, "metrics": {
            "transsafe-video": {"count": 10, "updated_utc": OLD},
            "transsafe-code": {"count": 5, "updated_utc": OLD},
        }}
        original = copy.deepcopy(existing)
        with patch.object(updater.resources, "github_stars", return_value=4), \
             patch.object(updater.resources, "bilibili_views", side_effect=URLError("offline")):
            result = updater.resources.update_metrics(existing)
        self.assertEqual(result["metrics"]["transsafe-video"], existing["metrics"]["transsafe-video"])
        self.assertEqual(result["metrics"]["transsafe-code"]["count"], 4)
        self.assertGreater(result["metrics"]["transsafe-code"]["updated_utc"], OLD)
        self.assertEqual(existing, original)

    def test_missing_scholar_paper_keeps_original_count_and_timestamp(self):
        old_item = {"count": 7, "updated_utc": OLD}
        existing = {"updated_utc": OLD, "citations": {"cac": old_item}}
        rows = [{"title": updater.scholar.PAPERS[0]["title"], "count": 16}]
        result = updater.scholar.build_citation_data(rows, existing, "https://scholar.google.com/")
        self.assertEqual(result["citations"]["cac"], old_item)
        self.assertGreater(result["citations"]["safetyfirst"]["updated_utc"], OLD)

    def test_scholar_failure_does_not_block_resources(self):
        existing = {RESOURCE_PATH: cache(), SCHOLAR_PATH: {"citations": {}}}
        with patch.object(updater.scholar, "fetch_profile_rows", side_effect=RuntimeError("unavailable")), \
             patch.object(updater.resources, "update_metrics", return_value=cache(NEW)):
            candidates, failures = updater.collect(existing)
        self.assertEqual(failures, ["Scholar"])
        self.assertNotIn(SCHOLAR_PATH, candidates)
        self.assertEqual(candidates[RESOURCE_PATH], cache(NEW))

    def test_actions_mode_never_fetches_scholar(self):
        with patch.object(updater.scholar, "fetch_profile_rows") as scholar_fetch, \
             patch.object(updater.resources, "update_metrics", return_value=cache(NEW)):
            updater.collect({RESOURCE_PATH: cache()}, resources_only=True)
        scholar_fetch.assert_not_called()


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="homepage-metrics-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.worker = self.root / "worker"
        self.other = self.root / "other"
        self.command(self.root, "init", "--bare", "--initial-branch=master", str(self.remote))
        self.command(self.root, "clone", str(self.remote), str(self.worker))
        self.command(self.worker, "config", "user.name", "Test User")
        self.command(self.worker, "config", "user.email", "test@example.com")
        self.write(self.worker, RESOURCE_PATH, json.dumps(cache()))
        self.write(self.worker, SCHOLAR_PATH, json.dumps({"updated_utc": OLD, "citations": {}}))
        self.write(self.worker, "index.html", "original homepage\n")
        self.commit(self.worker, "Initial homepage")
        self.command(self.worker, "push", "origin", "master")
        self.command(self.root, "clone", str(self.remote), str(self.other))
        self.command(self.other, "config", "user.name", "Other updater")
        self.command(self.other, "config", "user.email", "other@example.com")
        self.git = updater.Git(self.worker)
        self.addCleanup(self.git.close)

    def command(self, repo, *args, check=True):
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if check and result.returncode:
            self.fail(result.stderr)
        return result.stdout.strip()

    def write(self, repo, name, content):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, repo, message):
        self.command(repo, "add", ".")
        self.command(repo, "commit", "-m", message)

    def state(self):
        return (
            self.command(self.worker, "rev-parse", "HEAD"),
            self.command(self.worker, "ls-files", "--stage"),
            self.command(self.worker, "status", "--porcelain"),
            (self.worker / "index.html").read_bytes(),
            (self.worker / RESOURCE_PATH).read_bytes(),
        )

    def remote_cache(self):
        return json.loads(self.command(self.remote, "show", f"master:{RESOURCE_PATH}"))

    def test_publish_leaves_user_staged_and_unstaged_edits_untouched(self):
        self.write(self.worker, "index.html", "user's staged edit\n")
        self.command(self.worker, "add", "index.html")
        self.write(self.worker, "index.html", "user's further unstaged edit\n")
        before = self.state()
        updater.publish(self.git, {RESOURCE_PATH: cache(NEW)}, "Metrics", retry_delay=0)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.command(self.remote, "show", "master:index.html"), "original homepage")
        self.assertEqual(self.remote_cache(), cache(NEW))

    def test_publish_works_during_unrelated_unresolved_user_merge(self):
        self.command(self.worker, "checkout", "-b", "user-work")
        self.write(self.worker, "index.html", "branch edit\n")
        self.commit(self.worker, "User branch")
        self.command(self.worker, "checkout", "master")
        self.write(self.worker, "index.html", "other edit\n")
        self.commit(self.worker, "User master")
        self.command(self.worker, "merge", "user-work", check=False)
        self.assertTrue((self.worker / ".git/MERGE_HEAD").exists())
        before = self.state()
        updater.publish(self.git, {RESOURCE_PATH: cache(NEW)}, "Metrics", retry_delay=0)
        self.assertEqual(self.state(), before)
        self.assertTrue((self.worker / ".git/MERGE_HEAD").exists())
        self.assertEqual(self.remote_cache(), cache(NEW))

    def test_concurrent_remote_push_is_retried_without_data_loss(self):
        before = self.state()
        original_run = self.git.run
        pushes = []

        def push_with_race(*args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                if len(pushes) == 1:
                    remote_data = cache(NEWER, video=30)
                    remote_data["metrics"]["stars"]["updated_utc"] = OLD
                    self.write(self.other, RESOURCE_PATH, json.dumps(remote_data))
                    self.write(self.other, "index.html", "newly published homepage\n")
                    self.commit(self.other, "Concurrent update")
                    self.command(self.other, "push", "origin", "master")
            return original_run(*args, **kwargs)

        with patch.object(self.git, "run", side_effect=push_with_race):
            updater.publish(self.git, {RESOURCE_PATH: cache(NEW, video=20, stars=4)}, "Metrics", retry_delay=0)
        self.assertEqual(len(pushes), 2)
        result = self.remote_cache()
        self.assertEqual(result["metrics"]["video"]["count"], 30)
        self.assertEqual(result["metrics"]["stars"]["count"], 4)
        self.assertEqual(self.command(self.remote, "show", "master:index.html"), "newly published homepage")
        self.assertEqual(self.state(), before)

    def test_connection_lost_after_successful_push_does_not_duplicate_commit(self):
        original_run = self.git.run
        pushes = []

        def push_with_lost_response(*args, **kwargs):
            result = original_run(*args, **kwargs)
            if args[0] == "push":
                pushes.append(args)
                raise RuntimeError("Connection lost after push")
            return result

        with patch.object(self.git, "run", side_effect=push_with_lost_response):
            updater.publish(self.git, {RESOURCE_PATH: cache(NEW)}, "Metrics", retry_delay=0)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(self.command(self.remote, "rev-list", "--count", "master"), "2")

    def test_failed_push_leaves_no_merge_or_rebase_to_block_next_run(self):
        before = self.state()
        original_run = self.git.run

        def reject_push(*args, **kwargs):
            if args[0] == "push":
                raise RuntimeError("offline")
            return original_run(*args, **kwargs)

        with patch.object(self.git, "run", side_effect=reject_push):
            with self.assertRaises(RuntimeError):
                updater.publish(self.git, {RESOURCE_PATH: cache(NEW)}, "Metrics", retry_delay=0)
        self.assertEqual(self.state(), before)
        self.assertFalse((self.worker / ".git/rebase-merge").exists())
        self.assertFalse((self.worker / ".git/MERGE_HEAD").exists())
        updater.publish(self.git, {RESOURCE_PATH: cache(NEW)}, "Metrics", retry_delay=0)
        self.assertEqual(self.remote_cache(), cache(NEW))


if __name__ == "__main__":
    unittest.main()
