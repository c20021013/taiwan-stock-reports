#!/usr/bin/env python3
"""Send generated Taiwan stock reports to Discord and LINE."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def latest_report(mode: str) -> tuple[Path, Path]:
    folder = REPORTS_DIR / mode
    markdown_files = sorted(
        folder.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not markdown_files:
        raise FileNotFoundError(f"找不到 {folder} 內的 Markdown 報告")
    markdown_path = markdown_files[0]
    html_path = markdown_path.with_suffix(".html")
    if not html_path.exists():
        raise FileNotFoundError(f"找不到對應 HTML 報告：{html_path}")
    return markdown_path, html_path


def parse_table(markdown: str, heading: str, limit: int) -> list[dict[str, str]]:
    section = markdown.split(heading, 1)
    if len(section) < 2:
        return []
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in section[1].splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and rows:
            break
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if header is None or ("代碼" in cells and "名稱" in cells):
            header = cells
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
        if len(rows) >= limit:
            break
    return rows


def parse_list_items(markdown: str, heading: str, limit: int) -> list[str]:
    section = markdown.split(heading, 1)
    if len(section) < 2:
        return []
    items: list[str] = []
    for line in section[1].splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and items:
            break
        if stripped.startswith("- "):
            items.append(stripped[2:])
        if len(items) >= limit:
            break
    return items


def build_summary(markdown: str, mode: str) -> str:
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    title = title_match.group(1) if title_match else "台股研究報告"
    tldr = parse_list_items(markdown, "## 今日盤後速覽 (TL;DR)", 3)
    international = parse_list_items(markdown, "## 國際情勢與台股影響", 3)
    stocks = parse_table(markdown, "## 建議投資股票及原因", 3)
    etfs = parse_table(markdown, "## 建議投資 ETF 及原因", 2)
    lines = [title, ""]
    if tldr:
        lines.append("今日盤後速覽 (TL;DR)")
        for item in tldr:
            lines.append(f"- {item}")
        lines.append("")
    if international:
        lines.append("國際情勢與台股影響")
        for item in international:
            lines.append(f"- {item}")
        lines.append("")
    if stocks:
        lines.append("建議投資股票及原因")
        for row in stocks:
            lines.append(
                f"- {row.get('代碼')} {row.get('名稱')}｜{row.get('分數')} 分｜"
                f"{row.get('20D/60D報酬') or row.get('20日報酬') or row.get('20日')}｜"
                f"{row.get('建議原因') or row.get('關鍵字新聞') or row.get('淨值變動') or row.get('OCI／淨值變動')}"
            )
    if etfs:
        lines.extend(["", "建議投資 ETF 及原因"])
        for row in etfs:
            lines.append(
                f"- {row.get('代碼')} {row.get('名稱')}｜{row.get('分數')} 分｜"
                f"{row.get('20D/60D報酬') or row.get('20日報酬') or row.get('20日')}｜{row.get('建議原因')}"
            )
    if mode == "weekly":
        lines.extend(["", "週末／週報使用最近交易日資料。"])
    lines.extend(
        [
            "",
            "本內容是依公開資料產生的一般性量化建議，"
            "不考慮個人財務狀況，亦不保證獲利。",
        ]
    )
    return "\n".join(lines)


def build_discord_notice(markdown: str, html_path: Path | None = None) -> str:
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    title = title_match.group(1) if title_match else "台股研究報告"
    if html_path and html_path.stem not in title:
        title = f"{title}｜{html_path.stem}"
    return f"{title}\n完整內容請開啟 HTML 報告。"


def encode_multipart(
    fields: dict[str, str], files: list[tuple[str, Path]]
) -> tuple[bytes, str]:
    boundary = f"----TaiwanStockReport{uuid.uuid4().hex}"
    body = bytearray()

    def add_line(value: bytes = b"") -> None:
        body.extend(value)
        body.extend(b"\r\n")

    for name, value in fields.items():
        add_line(f"--{boundary}".encode())
        add_line(f'Content-Disposition: form-data; name="{name}"'.encode())
        add_line()
        add_line(value.encode("utf-8"))

    for field_name, path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_filename = path.name.encode("ascii", "replace").decode("ascii")
        add_line(f"--{boundary}".encode())
        add_line(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{safe_filename}"'
            ).encode()
        )
        add_line(f"Content-Type: {content_type}".encode())
        add_line()
        body.extend(path.read_bytes())
        body.extend(b"\r\n")

    add_line(f"--{boundary}--".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def send_discord(
    webhook_url: str,
    summary: str,
    html_path: Path,
    report_url: str = "",
) -> None:
    content = summary
    if report_url:
        content += f"\n\n直接開啟 HTML 報告：{report_url}"
    payload = {
        "content": content[:1900],
        "username": "台股研究報告",
        "allowed_mentions": {"parse": []},
        "attachments": [
            {
                "id": 0,
                "filename": html_path.name,
                "description": "台股研究 HTML 報告",
            }
        ],
    }
    body, content_type = encode_multipart(
        {"payload_json": json.dumps(payload, ensure_ascii=False)},
        [("files[0]", html_path)],
    )
    separator = "&" if "?" in webhook_url else "?"
    request = urllib.request.Request(
        f"{webhook_url}{separator}wait=true",
        data=body,
        headers={
            "Content-Type": content_type,
            "User-Agent": "TaiwanStockResearch/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in {200, 204}:
            raise RuntimeError(f"Discord 回傳 HTTP {response.status}")


def public_report_url(base_url: str, html_path: Path) -> str:
    relative = html_path.relative_to(ROOT).as_posix()
    quoted = "/".join(urllib.parse.quote(part) for part in relative.split("/"))
    return f"{base_url.rstrip('/')}/{quoted}"


def send_line(
    access_token: str,
    target_id: str,
    summary: str,
    report_url: str,
) -> None:
    text = summary
    if report_url:
        text += f"\n\nHTML 報告：{report_url}"
    payload = {
        "to": target_id,
        "messages": [{"type": "text", "text": text[:5000]}],
    }
    request = urllib.request.Request(
        LINE_PUSH_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "TaiwanStockResearch/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"LINE 回傳 HTTP {response.status}")


def notify(mode: str, dry_run: bool = False) -> int:
    markdown_path, html_path = latest_report(mode)
    markdown = markdown_path.read_text(encoding="utf-8")
    summary = build_summary(markdown, mode)
    discord_notice = build_discord_notice(markdown, html_path)

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    line_target = os.environ.get("LINE_TARGET_ID", "").strip()
    public_base = os.environ.get("REPORT_PUBLIC_BASE_URL", "").strip()
    report_url = public_report_url(public_base, html_path) if public_base else ""

    if dry_run:
        print(discord_notice)
        print(f"\nHTML: {html_path}")
        print(f"Discord: {'configured' if discord_url else 'not configured'}")
        print(
            "LINE: "
            + (
                "configured"
                if line_token and line_target
                else "not configured"
            )
        )
        if line_token and line_target:
            print(f"LINE HTML URL: {report_url or 'not configured'}")
        return 0

    sent = 0
    errors: list[str] = []
    if discord_url:
        try:
            send_discord(discord_url, discord_notice, html_path, report_url)
            print(f"Discord notification sent: {html_path.name}")
            sent += 1
        except (urllib.error.URLError, RuntimeError) as exc:
            errors.append(f"Discord：{exc}")

    if line_token and line_target:
        try:
            send_line(line_token, line_target, summary, report_url)
            print("LINE notification sent")
            sent += 1
        except (urllib.error.URLError, RuntimeError) as exc:
            errors.append(f"LINE：{exc}")

    if not discord_url and not (line_token and line_target):
        print("Notification skipped: no Discord or LINE credentials configured")
        return 0

    if errors:
        for error in errors:
            print(f"Notification error: {error}", file=sys.stderr)
        return 1
    print(f"Notifications sent: {sent}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("daily", "weekly"),
        default="daily",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(notify(args.mode, args.dry_run))
