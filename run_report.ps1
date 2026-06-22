param(
    [ValidateSet("daily", "weekly")]
    [string]$Mode = "daily"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = "C:\Users\c2002\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path -LiteralPath $BundledPython) { $BundledPython } else { "python" }
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "scheduler-$Mode.log"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

foreach ($Name in @(
    "DISCORD_WEBHOOK_URL",
    "FINMIND_TOKEN",
    "GITHUB_REPORT_REPOSITORY",
    "GITHUB_REPORT_TOKEN",
    "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_TARGET_ID",
    "REPORT_PUBLIC_BASE_URL"
)) {
    $Value = [Environment]::GetEnvironmentVariable(
        $Name,
        [EnvironmentVariableTarget]::User
    )
    if (![string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

Push-Location $ProjectDir
try {
    "[$(Get-Date -Format s)] Starting $Mode report" |
        Add-Content -LiteralPath $LogFile -Encoding UTF8
    & $Python ".\stock_report.py" --mode $Mode 2>&1 | ForEach-Object {
        $Line = "$_"
        Write-Output $Line
        Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
    }
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "Report generation failed with exit code $ExitCode"
    }
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python ".\publish_report.py" --mode $Mode 2>&1 | ForEach-Object {
        $Line = "$_"
        Write-Output $Line
        Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
    }
    $PublishExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($PublishExitCode -ne 0) {
        "[$(Get-Date -Format s)] GitHub publish failed; local report is available." |
            Add-Content -LiteralPath $LogFile -Encoding UTF8
    }
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python ".\notify_report.py" --mode $Mode 2>&1 | ForEach-Object {
        $Line = "$_"
        Write-Output $Line
        Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
    }
    $NotificationExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($NotificationExitCode -ne 0) {
        "[$(Get-Date -Format s)] Notification failed; local report is available." |
            Add-Content -LiteralPath $LogFile -Encoding UTF8
    }
    "[$(Get-Date -Format s)] Completed $Mode report" |
        Add-Content -LiteralPath $LogFile -Encoding UTF8
}
catch {
    "[$(Get-Date -Format s)] Failed: $($_.Exception.Message)" |
        Add-Content -LiteralPath $LogFile -Encoding UTF8
    throw
}
finally {
    Pop-Location
}
