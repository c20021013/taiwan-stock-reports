param(
    [string]$DiscordWebhookUrl = "",
    [string]$LineChannelAccessToken = "",
    [string]$LineTargetId = "",
    [string]$ReportPublicBaseUrl = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Scope = [System.EnvironmentVariableTarget]::User

if ($Remove) {
    foreach ($Name in @(
        "DISCORD_WEBHOOK_URL",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_TARGET_ID",
        "REPORT_PUBLIC_BASE_URL"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null, $Scope)
    }
    Write-Host "Notification settings removed."
    exit 0
}

if ($DiscordWebhookUrl) {
    [Environment]::SetEnvironmentVariable(
        "DISCORD_WEBHOOK_URL",
        $DiscordWebhookUrl,
        $Scope
    )
}
if ($LineChannelAccessToken) {
    [Environment]::SetEnvironmentVariable(
        "LINE_CHANNEL_ACCESS_TOKEN",
        $LineChannelAccessToken,
        $Scope
    )
}
if ($LineTargetId) {
    [Environment]::SetEnvironmentVariable(
        "LINE_TARGET_ID",
        $LineTargetId,
        $Scope
    )
}
if ($ReportPublicBaseUrl) {
    [Environment]::SetEnvironmentVariable(
        "REPORT_PUBLIC_BASE_URL",
        $ReportPublicBaseUrl,
        $Scope
    )
}

Write-Host "Notification settings saved for the current Windows user."
Write-Host "Discord sends the HTML file as an attachment."
Write-Host "LINE sends a summary and an HTML URL when a public base URL is configured."
