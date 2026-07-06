import os
import unittest
from pathlib import Path
from unittest.mock import patch

import notify_report


SAMPLE = """# 台股每日研究報告

## 盤前速覽（以前一交易日資料為主）(TL;DR)

- 大盤結構：🟢 前一交易日跌多漲少，電子股 63.5%、金融股 2.6%、傳產股 33.9%。
- 籌碼動向：🔴 外資現貨買超 20.0 億。
- 風險提示：⚠️ VIX 上升，短線波動加劇。

## 國際情勢與台股影響

- 綜合判斷：國際盤勢對台股為「偏多」。
- 科技與半導體：SOX 走強，對台股電子供應鏈偏多。

## 高優先研究股票及研究理由

| 代碼 | 名稱 | 分數 | 研究狀態 | 20日報酬 | 60日報酬 | 月營收年增率 (YoY) | 本益比 (PE) | 研究理由 |
|---|---|---:|---|---:|---:|---:|---:|---|
| 2330 | 台積電 | 88.1 | 高優先研究名單 | 8.0% | 20.0% | 30.0% | 25.0 | 趨勢向上 |
| 2308 | 台達電 | 80.0 | 高優先研究名單 | 6.0% | 15.0% | 20.0% | 30.0 | 營收成長 |

## ETF 觀察名單及研究理由

| ETF 代碼 | ETF 名稱 | 分數 | 觀察狀態 | 20D/60D報酬 | 追蹤指數 | 類型 | 近四季配息 | 官方折溢價 | 前十大成分股 | 管理費與保管費 | 觀察理由 |
|---|---|---:|---|---:|---|---|---|---|---|---|---|
| 0050 | 元大台灣50 | 75.0 | 觀察名單 | 5.0% / 10.0% | 資料不足 | 市值型 | 資料不足 | 資料不足 | 資料不足 | 資料不足 | AI 與半導體需求 |
"""


class NotificationTests(unittest.TestCase):
    def test_discord_notice_only_points_to_full_html(self):
        notice = notify_report.build_discord_notice(
            SAMPLE,
            Path("reports/daily/2026-06-24.html"),
        )
        self.assertEqual(
            notice,
            "台股每日研究報告｜2026-06-24\n完整內容請開啟 HTML 報告。",
        )
        self.assertNotIn("2330", notice)
        self.assertNotIn("VIX", notice)
        self.assertNotIn("高優先研究名單", notice)

    def test_summary_contains_stock_and_etf(self):
        summary = notify_report.build_summary(SAMPLE, "daily")
        self.assertIn("2330 台積電", summary)
        self.assertIn("0050 元大台灣50", summary)
        self.assertIn("盤前速覽", summary)
        self.assertIn("VIX 上升", summary)
        self.assertIn("國際情勢與台股影響", summary)
        self.assertIn("SOX 走強", summary)
        self.assertIn("不保證獲利", summary)

    def test_public_url_uses_report_relative_path(self):
        path = notify_report.ROOT / "reports" / "daily" / "2026-06-13.html"
        url = notify_report.public_report_url("https://example.com/base/", path)
        self.assertEqual(
            url,
            "https://example.com/base/reports/daily/2026-06-13.html",
        )

    def test_scheduled_github_action_can_notify(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "schedule",
        }
        with patch.dict(os.environ, env, clear=True):
            allowed, reason = notify_report.notifications_allowed()
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_manual_notify_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            allowed, reason = notify_report.notifications_allowed()
        self.assertFalse(allowed)
        self.assertIn("manual/local notification is disabled", reason)

    def test_manual_notify_opt_in_can_notify(self):
        with patch.dict(os.environ, {"ALLOW_MANUAL_NOTIFY": "true"}, clear=True):
            allowed, reason = notify_report.notifications_allowed()
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_multipart_contains_payload_and_file(self):
        report = Path(__file__)
        body, content_type = notify_report.encode_multipart(
            {"payload_json": '{"content":"test"}'},
            [("files[0]", report)],
        )
        self.assertIn(b'payload_json', body)
        self.assertIn(b'files[0]', body)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))

    def test_discord_payload_includes_public_report_url(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("urllib.request.urlopen", return_value=Response()) as mocked:
            notify_report.send_discord(
                "https://discord.com/api/webhooks/test",
                "摘要",
                Path(__file__),
                "https://example.com/report.html",
            )
        request = mocked.call_args.args[0]
        self.assertIn(b"https://example.com/report.html", request.data)


if __name__ == "__main__":
    unittest.main()
