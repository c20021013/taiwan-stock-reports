# Taiwan Stock Reports Codex Handoff

This folder is the Taiwan stock research report automation project.

## What This Project Does

- Generates Taiwan stock daily research reports as standalone HTML files.
- Publishes the latest report to GitHub Pages.
- Sends a short Discord notification that links to the full HTML report.
- Runs cloud automation through GitHub Actions, so a local computer does not need to stay on.

## Current Public URLs

- Main site: <https://c20021013.github.io/taiwan-stock-reports/>
- Latest report: <https://c20021013.github.io/taiwan-stock-reports/reports/latest.html>
- Daily latest: <https://c20021013.github.io/taiwan-stock-reports/reports/daily/latest.html>
- Weekly latest: <https://c20021013.github.io/taiwan-stock-reports/reports/weekly/latest.html>

Do not use `https://c20021013.github.io/` as the report URL. This is a project Pages site, so the path must include `/taiwan-stock-reports/`.

## Current Cloud Schedule

Defined in `.github/workflows/taiwan-stock-reports.yml`.

- Monday to Friday 08:00 Asia/Taipei: daily report.
- Sunday 21:00 Asia/Taipei: weekly summary report.
- Saturday and Sunday morning double-day reports are disabled.

The workflow starts early in UTC and waits until the exact Taiwan time before generating the report.

## What Is In The Transfer Package

- Source code:
  - `stock_report.py`
  - `publish_report.py`
  - `notify_report.py`
  - `validate_report.py`
- GitHub Actions workflow:
  - `.github/workflows/taiwan-stock-reports.yml`
- Config and helper scripts:
  - `config.json`
  - `run_report.ps1`
  - `setup_notifications.ps1`
  - `setup_windows_tasks.ps1`
- Existing generated HTML reports under `reports/`.
- Tests under `tests/`.
- Environment template:
  - `.env.example`
- This handoff guide.

The transfer zip intentionally excludes caches, Python bytecode, and local secrets.

## Important Secrets

Real tokens and webhook URLs are not included in the package.

For GitHub Actions, configure these in:

`GitHub repo -> Settings -> Secrets and variables -> Actions`

Secrets:

- `DISCORD_WEBHOOK_URL`: Discord webhook for the report channel.
- `FINMIND_TOKEN`: optional, improves API quota.
- `LINE_CHANNEL_ACCESS_TOKEN`: optional, only if LINE is used.
- `LINE_TARGET_ID`: optional, only if LINE is used.

Variables:

- `REPORT_PUBLIC_BASE_URL`: `https://c20021013.github.io/taiwan-stock-reports`
- `GITHUB_REPORT_REPOSITORY`: `c20021013/taiwan-stock-reports`

Local publishing from a new computer is blocked unless you intentionally set:

- `ALLOW_MANUAL_PUBLISH=true`
- `GITHUB_REPORT_TOKEN=<your token>`

This is deliberate, to prevent manual Codex runs from overwriting the public latest report by accident.

## Recommended Setup On The New Computer

Best option:

```powershell
git clone https://github.com/c20021013/taiwan-stock-reports.git
cd taiwan-stock-reports
```

Alternative option:

Unzip the transfer package and open the extracted project folder in Codex.

If you also received `taiwan-stock-reports.bundle`, restore Git history with:

```powershell
git clone .\taiwan-stock-reports.bundle taiwan-stock-reports
cd taiwan-stock-reports
git remote set-url origin https://github.com/c20021013/taiwan-stock-reports.git
```

## Verify On The New Computer

Run:

```powershell
python -m py_compile stock_report.py notify_report.py publish_report.py validate_report.py
python -m unittest discover -s tests -v
```

Expected result: all tests pass.

## Generate A Local Report

Daily:

```powershell
python stock_report.py --mode daily
```

Weekly:

```powershell
python stock_report.py --mode weekly
```

The generated HTML will appear under:

- `reports/daily/`
- `reports/weekly/`

To update local `index.html`, `reports/latest.html`, and mode-specific `latest.html` during a local run:

```powershell
$env:UPDATE_REPORT_ALIASES = "true"
python stock_report.py --mode daily
```

Do not set `UPDATE_REPORT_ALIASES=true` for random experiments unless you really want aliases changed.

## Publish Manually From The New Computer

Manual local publishing is normally unnecessary because GitHub Actions publishes automatically.

If you intentionally need to publish from the new computer:

```powershell
$env:ALLOW_MANUAL_PUBLISH = "true"
$env:GITHUB_REPORT_TOKEN = "your GitHub token"
$env:GITHUB_REPORT_REPOSITORY = "c20021013/taiwan-stock-reports"
python publish_report.py --mode daily
```

## Send A Manual Discord Notification

```powershell
$env:ALLOW_MANUAL_NOTIFY = "true"
$env:DISCORD_WEBHOOK_URL = "your Discord webhook"
$env:REPORT_PUBLIC_BASE_URL = "https://c20021013.github.io/taiwan-stock-reports"
python notify_report.py --mode daily
```

## Operational Notes

- The cloud schedule continues even if Codex is closed.
- Moving to a new computer does not change GitHub Actions.
- The new computer is mainly for maintenance, manual generation, testing, and future code changes.
- Keep real tokens in GitHub Secrets or local environment variables, not inside project files.
- If the website seems stale, test with a cache-busting URL like:

```text
https://c20021013.github.io/taiwan-stock-reports/?cb=manual-check
```

