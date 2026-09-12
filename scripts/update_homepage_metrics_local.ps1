param(
  [string]$Proxy = "",
  [switch]$UseBrowser,
  [string]$CommitMessage = "Update homepage metric caches"
)

$ErrorActionPreference = "Stop"

$Updater = Join-Path $PSScriptRoot "update_homepage_metrics.py"
$UpdaterArguments = @($Updater, "--commit-message", $CommitMessage)
if ($Proxy) {
  $UpdaterArguments += @("--proxy", $Proxy)
}
if ($UseBrowser) {
  $UpdaterArguments += "--use-browser"
}

python @UpdaterArguments
exit $LASTEXITCODE
