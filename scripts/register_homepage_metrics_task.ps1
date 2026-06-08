param(
  [string]$TaskName = "Update homepage metrics",
  [string]$Time = "09:30",
  [int]$Days = 3,
  [string]$Proxy = "",
  [switch]$UseBrowser
)

$ErrorActionPreference = "Stop"

$Updater = Resolve-Path (Join-Path $PSScriptRoot "update_homepage_metrics_local.ps1")
$ArgumentParts = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$Updater`""
)

if ($Proxy) {
  $ArgumentParts += @("-Proxy", "`"$Proxy`"")
}
if ($UseBrowser) {
  $ArgumentParts += "-UseBrowser"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($ArgumentParts -join " ")
$Trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $Days -At $Time
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel LeastPrivilege

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force | Out-Null
Write-Host "Registered scheduled task '$TaskName' every $Days day(s) at $Time."
