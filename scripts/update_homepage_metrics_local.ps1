param(
  [string]$Proxy = "",
  [switch]$UseBrowser,
  [string]$CommitMessage = "Update homepage metric caches"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot

try {
  $initialStatus = git status --porcelain
  if ($initialStatus) {
    Write-Error "Working tree is not clean. Commit or stash local changes before running the scheduled updater."
  }

  git pull --rebase origin master

  if ($Proxy) {
    $env:SCHOLAR_PROXY = $Proxy
  }
  if ($UseBrowser) {
    $env:SCHOLAR_BROWSER_FETCH = "1"
  }

  python scripts\update_scholar_citations.py --strict --retries 3 --retry-delay 5
  python scripts\update_resource_metrics.py

  git diff --quiet -- data/scholar-citations.json data/resource-metrics.json
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Metric caches unchanged."
    exit 0
  }

  git add data/scholar-citations.json data/resource-metrics.json
  git commit -m $CommitMessage
  git push origin master
}
finally {
  Pop-Location
}
