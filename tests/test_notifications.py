import unittest
from pathlib import Path
from unittest.mock import patch

import notify_report


SAMPLE = """# 台股每日研究報告

## 今日盤後速覽 (TL;DR)

- 大盤結構：🟢 今日跌多漲少，電子股 63.5%、金融股 2.6%、傳產股 33.9%。
- 籌碼動向：🔴 外資現貨買超 20.0 億。
- 風險提示：⚠️ VIX 上升，短線波動加劇。

## 國際情勢與台股影響

- 綜合判斷：國際盤勢對台股為「偏多」。
- 科技與半導體：SOX 走強，對台股電子供應鏈偏多。

## 建議投資股票及原因

| 代碼 | 名稱 | 分數 | 建議 | 20日報酬 | 60日報酬 | 月營收年增率 (YoY) | 本益比 (PE) | 建議原因 |
|---|---|---:|---|---:|---:|---:|---:|---|
| 2330 | 台積電 | 88.1 | 建議投資 | 8.0% | 20.0% | 30.0% | 25.0 | 趨勢向上 |
| 2308 | 台達電 | 80.0 | 建議投資 | 6.0% | 15.0% | 20.0% | 30.0 | 營收成長 |

## 建議投資 ETF 及原因

| 代碼 | 名稱 | 分數 | 建議 | 20D/60D報酬 | 主要曝險/成分股主題 | 折溢價提醒 | 建議原因 |
|---|---|---:|---|---:|---|---|---|
| 0050 | 元大台灣50 | 75.0 | 建議投資 | 5.0% / 10.0% | 台灣大型權值股 | 買進前確認 iNAV 與市價折溢價 | AI 與半導體需求 |
"""


class NotificationTests(unittest.TestCase):
    def test_summary_contains_stock_and_etf(self):
        summary = notify_report.build_summary(SAMPLE, "daily")
        self.assertIn("2330 台積電", summary)
        self.assertIn("0050 元大台灣50", summary)
        self.assertIn("今日盤後速覽", summary)
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
