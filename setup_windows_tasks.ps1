param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ProjectDir "run_report.ps1"
$PowerShell = (Get-Command powershell.exe).Source
$TaskPrefix = "TaiwanStockResearch"

if ($Remove) {
    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Write-Host "Taiwan stock research tasks removed."
    exit 0
}

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

function Register-ReportTask {
    param(
        [string]$Name,
        [string]$Mode,
        [Microsoft.Management.Infrastructure.CimInstance]$Trigger
    )

    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Mode $Mode"
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments
    Register-ScheduledTask `
        -TaskName $Name `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Generate Taiwan stock $Mode research report" `
        -Force | Out-Null
}

$DailyTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At 08:00

$WeeklyTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Sunday `
    -At 21:00

Register-ReportTask -Name "${TaskPrefix}-Weekday" -Mode "daily" -Trigger $DailyTrigger
Register-ReportTask -Name "${TaskPrefix}-Weekly" -Mode "weekly" -Trigger $WeeklyTrigger

Write-Host "Tasks created:"
Write-Host "- Monday-Friday 08:00: daily report"
Write-Host "- Sunday 21:00: weekly summary"
