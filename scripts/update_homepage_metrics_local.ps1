param(
  [string]$Proxy = "",
  [switch]$UseBrowser,
  [string]$CommitMessage = "Update homepage metric caches"
)

$ErrorActionPreference = "Stop"

function Assert-NativeSuccess {
  param([string]$Operation)

  if ($LASTEXITCODE -ne 0) {
    throw "$Operation failed with exit code $LASTEXITCODE."
  }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot

try {
  $GitDir = git rev-parse --git-dir
  Assert-NativeSuccess "Locating the Git directory"
  $GitDir = Resolve-Path $GitDir

  $PendingGitOperations = @(
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD"
  )
  foreach ($OperationPath in $PendingGitOperations) {
    if (Test-Path (Join-Path $GitDir $OperationPath)) {
      throw "An unfinished Git operation exists at $OperationPath. Resolve it before updating metrics."
    }
  }

  $CurrentBranch = git branch --show-current
  Assert-NativeSuccess "Checking the current Git branch"
  if (-not $CurrentBranch) {
    throw "The repository is not on a branch."
  }

  $initialStatus = git status --porcelain
  Assert-NativeSuccess "Checking the working tree"
  if ($initialStatus) {
    Write-Error "Working tree is not clean. Commit or stash local changes before running the scheduled updater."
  }

  $GitNetworkArgs = @()
  if ($Proxy) {
    $GitNetworkArgs = @(
      "-c", "http.https://github.com.proxy=$Proxy",
      "-c", "http.version=HTTP/1.1"
    )
  }

  git @GitNetworkArgs pull --rebase origin master
  Assert-NativeSuccess "Pulling the latest homepage changes"

  if ($Proxy) {
    $env:SCHOLAR_PROXY = $Proxy
  }
  if ($UseBrowser) {
    $env:SCHOLAR_BROWSER_FETCH = "1"
  }

  python scripts\update_scholar_citations.py --strict --retries 3 --retry-delay 5
  Assert-NativeSuccess "Updating Google Scholar citations"
  python scripts\update_resource_metrics.py
  Assert-NativeSuccess "Updating resource metrics"

  git diff --quiet -- data/scholar-citations.json data/resource-metrics.json
  $DiffExitCode = $LASTEXITCODE
  if ($DiffExitCode -eq 0) {
    Write-Host "Metric caches unchanged."
    exit 0
  }
  if ($DiffExitCode -ne 1) {
    throw "Checking metric cache changes failed with exit code $DiffExitCode."
  }

  git add data/scholar-citations.json data/resource-metrics.json
  Assert-NativeSuccess "Staging metric caches"
  git commit -m $CommitMessage
  Assert-NativeSuccess "Committing metric caches"
  git @GitNetworkArgs push origin master
  Assert-NativeSuccess "Pushing metric caches"
}
finally {
  Pop-Location
}
