#!/usr/bin/env python3
"""Publish generated HTML reports to a GitHub Pages repository."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
GITHUB_API = "https://api.github.com"
DEFAULT_REPOSITORY = "c20021013/taiwan-stock-reports"
USER_AGENT = "TaiwanStockResearch/1.0"
REQUIRED_CURRENT_REPORT_MARKERS = (
    "finance-dashboard",
    "Next Trading Day",
    "方向機率",
)


def request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def get_content_sha(repository: str, path: str, token: str) -> str | None:
    quoted_path = urllib.parse.quote(path, safe="/")
    url = f"{GITHUB_API}/repos/{repository}/contents/{quoted_path}"
    try:
        payload = request_json("GET", url, token)
        return payload.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def put_text_file(
    repository: str,
    path: str,
    content: str,
    token: str,
    message: str,
) -> None:
    quoted_path = urllib.parse.quote(path, safe="/")
    url = f"{GITHUB_API}/repos/{repository}/contents/{quoted_path}"
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    sha = get_content_sha(repository, path, token)
    if sha:
        payload["sha"] = sha
    request_json("PUT", url, token, payload)


def latest_report(mode: str) -> Path:
    folder = REPORTS_DIR / mode
    files = sorted(folder.glob("*.html"), key=lambda path: path.name, reverse=True)
    if not files:
        raise FileNotFoundError(f"找不到 {folder} 內的 HTML 報告")
    return files[0]


def report_repository_path(html_path: Path) -> str:
    return html_path.relative_to(ROOT).as_posix()


def validate_current_report_html(content: str, repository_path: str) -> None:
    """Block stale/simple reports from replacing the public latest report."""
    missing = [
        marker for marker in REQUIRED_CURRENT_REPORT_MARKERS if marker not in content
    ]
    if missing:
        raise RuntimeError(
            f"Refusing to publish {repository_path}: missing required HTML markers "
            f"{', '.join(missing)}"
        )


def publish_file(html_path: Path, repository: str, token: str) -> str:
    repository_path = report_repository_path(html_path)
    content = html_path.read_text(encoding="utf-8")
    validate_current_report_html(content, repository_path)
    put_text_file(
        repository,
        repository_path,
        content,
        token,
        f"Publish {repository_path}",
    )
    put_text_file(
        repository,
        "index.html",
        content,
        token,
        "Update latest report",
    )
    put_text_file(repository, ".nojekyll", "", token, "Configure GitHub Pages")
    return repository_path


def publish_all(repository: str, token: str) -> list[str]:
    paths: list[str] = []
    html_files = sorted(REPORTS_DIR.glob("*/*.html"))
    for html_path in html_files:
        repository_path = report_repository_path(html_path)
        put_text_file(
            repository,
            repository_path,
            html_path.read_text(encoding="utf-8"),
            token,
            f"Publish {repository_path}",
        )
        paths.append(repository_path)
    latest_html = REPORTS_DIR / "latest.html"
    if latest_html.exists():
        put_text_file(
            repository,
            "index.html",
            latest_html.read_text(encoding="utf-8"),
            token,
            "Publish latest report",
        )
    put_text_file(repository, ".nojekyll", "", token, "Configure GitHub Pages")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("daily", "weekly"),
        default="daily",
    )
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("GITHUB_REPORT_TOKEN", "").strip()
    repository = os.environ.get(
        "GITHUB_REPORT_REPOSITORY", DEFAULT_REPOSITORY
    ).strip()
    if not token:
        print("GitHub publish skipped: GITHUB_REPORT_TOKEN is not configured")
        return 0
    try:
        if args.all:
            paths = publish_all(repository, token)
            print(f"Published {len(paths)} archived HTML reports")
        else:
            path = publish_file(latest_report(args.mode), repository, token)
            print(f"Published HTML report: {path}")
        return 0
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        print(f"GitHub publish error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
