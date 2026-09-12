<a href="https://joejoeovo.github.io/">Homepage</a>

## Automatic metrics

- The Windows task `Update homepage metrics` runs every three days at 20:00
  local time. It fetches Google Scholar citations, Bilibili views, and GitHub
  stars. Google Scholar is only contacted by the local task.
- GitHub Actions also updates views and stars using the existing schedule:
  `17 20 */3 * *` (20:17 UTC on days 1, 4, 7, etc. of each month).
- The Windows task catches up when available after a missed run and retries
  failures up to three times, 30 minutes apart. The computer must be on and
  the user logged in; the configured proxy must be available for Scholar.

Run a local update immediately from PowerShell:

```powershell
.\scripts\update_homepage_metrics_local.ps1 -Proxy "http://127.0.0.1:7890"
```

Register the Windows task on a new computer (the existing task does not need
to be registered again just because the scripts changed):

```powershell
.\scripts\register_homepage_metrics_task.ps1 -Proxy "http://127.0.0.1:7890"
```

The publisher reads the latest remote caches and builds a commit with a
temporary Git index. It never pulls, rebases, stages files in, or switches the
user's checkout. If another writer pushes first, it refetches and retries,
preserving the newest successful observation for each paper/video/repository.
Uncommitted homepage edits therefore do not block scheduled updates or get
included in metric commits. Local `data/` files stay at the checkout's version;
the published caches on GitHub are the source for the live site. A normal
`git pull --ff-only` can bring a clean checkout up to date.

Each successfully fetched entry records its own `updated_utc`. Failed entries
retain their previous count and timestamp; no alternate citation provider is
used. The cache-level time is the latest recorded successful observation, not
a guarantee that every entry was fetched at that time. Legacy entries without
an individual timestamp have an unknown last successful fetch until refreshed.
Scholar failures do not prevent publishing successfully fetched resource data.

Local runs write diagnostic logs to `logs/homepage-metrics-*.log` (Git-ignored).
The Task Scheduler result is nonzero when Scholar fails, all resources fail,
or publication fails, allowing the scheduled retry to run.

Regression tests, including competing Git publishers and a dirty/conflicted
user checkout, run entirely against temporary local repositories:

```powershell
python -m unittest discover -s scripts/tests -v
```
