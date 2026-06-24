#!/usr/bin/env python3
"""Validate generated Taiwan stock reports before publishing or notifying."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"


def latest_report(mode: str) -> tuple[Path, Path]:
    folder = REPORTS_DIR / mode
    markdown_files = sorted(
        folder.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not markdown_files:
        raise FileNotFoundError(f"No Markdown report found in {folder}")
    markdown_path = markdown_files[0]
    html_path = markdown_path.with_suffix(".html")
    if not html_path.exists():
        raise FileNotFoundError(f"Missing HTML report: {html_path}")
    return markdown_path, html_path


def section(markdown: str, heading: str, next_heading: str | None = None) -> str:
    parts = markdown.split(heading, 1)
    if len(parts) < 2:
        raise AssertionError(f"Missing section: {heading}")
    body = parts[1]
    if next_heading:
        return body.split(next_heading, 1)[0]
    return body


def parse_report_date(path: Path) -> date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", path.name)
    if not match:
        return None
    return date.fromisoformat(match.group(0))


def validate(mode: str) -> list[str]:
    markdown_path, html_path = latest_report(mode)
    markdown = markdown_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    errors: list[str] = []

    required_text = [
        "## 今日盤後速覽 (TL;DR)",
        "## 國際情勢與台股影響",
        "## 2.5 台股大盤與資金健康度",
        "## 2.6 籌碼與信用交易動態",
        "## 建議投資 ETF 及原因",
        "月營收年增率 (YoY)",
        "本益比 (PE)",
        "股淨比 (PB)",
        "淨值季變動 (QoQ)",
        "實際折溢價幅度 (%)",
        "非純散戶",
        "TAIFEX OpenAPI",
        "TDCC OpenAPI",
        "VIX",
    ]
    for text in required_text:
        if text not in markdown:
            errors.append(f"Missing required report text: {text}")

    if not any(marker in markdown for marker in ("🔴", "🟢", "⚠️")):
        errors.append("Missing visual direction or warning markers")
    if "**" not in markdown or "<strong>" not in html:
        errors.append("Missing bold emphasis in Markdown or HTML")
    if "QQ" in markdown or "OCI／淨值變動" in markdown:
        errors.append("Ambiguous financial terminology is present")
    if "股市爆料同學會" in markdown or "CMoney投資網誌" in markdown:
        errors.append("Low-quality forum news is present")

    try:
        etf_section = section(
            markdown, "## 建議投資 ETF 及原因", "## 投資建議詳情"
        )
        if "月營收YoY" in etf_section or "| PE |" in etf_section:
            errors.append("ETF table still contains stock-only columns")
        if (
            "主要曝險/成分股主題" not in etf_section
            or "實際折溢價幅度 (%)" not in etf_section
        ):
            errors.append("ETF table is missing ETF-specific columns")
    except AssertionError as exc:
        errors.append(str(exc))

    try:
        details_section = section(markdown, "## 投資建議詳情", "## 使用方式")
        etf_detail_blocks = [
            block
            for block in details_section.split("\n### ")
            if re.match(r"0\d{3,5}", block.strip())
        ]
        etf_detail_text = "\n".join(etf_detail_blocks)
        if "月營收年增：—" in etf_detail_text or "本益比：—" in etf_detail_text:
            errors.append("ETF detail block still contains empty stock-only metrics")
    except AssertionError as exc:
        errors.append(str(exc))

    report_date = parse_report_date(markdown_path)
    if report_date:
        try:
            dated_sections = section(
                markdown, "## 2.5 台股大盤與資金健康度", "## 建議投資股票及原因"
            )
            for found in re.findall(r"20\d{2}-\d{2}-\d{2}", dated_sections):
                if date.fromisoformat(found) > report_date:
                    errors.append(
                        f"Chip data date {found} is later than report date {report_date}"
                    )
        except AssertionError as exc:
            errors.append(str(exc))

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "weekly"), default="daily")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate(args.mode)
    if errors:
        for error in errors:
            print(f"Report validation error: {error}", file=sys.stderr)
        return 1
    print(f"Report validation passed for mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
