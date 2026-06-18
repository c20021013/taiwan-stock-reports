# Cloud Schedule Setup

The report source code, tests, validator, and workflow template are in this
repository. The only blocked step is GitHub's protected workflow/secret write
permission.

## Current Blocker

GitHub returned `403 Resource not accessible by personal access token` for:

- Creating `.github/workflows/taiwan-stock-reports.yml`
- Writing the `DISCORD_WEBHOOK_URL` Actions secret

This means the current token can write normal repository files, but it cannot
manage GitHub Actions workflows or repository secrets.

## One-Time Enablement

1. Create this file in GitHub:

   `.github/workflows/taiwan-stock-reports.yml`

2. Copy the full contents from:

   `cloud-workflow-template.yml`

3. Add this repository secret:

   `DISCORD_WEBHOOK_URL`

   Use the Discord webhook URL for the report channel.

4. Optional secret:

   `FINMIND_TOKEN`

   The report can run without it, but a token improves API quota reliability.

5. Optional variable:

   `REPORT_PUBLIC_BASE_URL`

   Default:

   `https://c20021013.github.io/taiwan-stock-reports`

## Schedule

- Monday-Friday 08:00 Asia/Taipei: daily report
- Sunday 21:00 Asia/Taipei: weekly summary

## Safety Gate

The cloud workflow runs these checks before publishing or sending Discord:

```bash
python -m py_compile stock_report.py notify_report.py publish_report.py validate_report.py
python -m unittest discover -s tests -v
python stock_report.py --mode "$mode"
python validate_report.py --mode "$mode"
```

If any check fails, GitHub Actions stops before publish/notification.
