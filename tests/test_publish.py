import unittest
from pathlib import Path

import publish_report


class PublishTests(unittest.TestCase):
    def test_report_repository_path(self):
        path = publish_report.ROOT / "reports" / "daily" / "2026-06-13.html"
        self.assertEqual(
            publish_report.report_repository_path(path),
            "reports/daily/2026-06-13.html",
        )

    def test_latest_report_is_html(self):
        path = publish_report.latest_report("daily")
        self.assertEqual(path.suffix, ".html")
        self.assertTrue(path.is_relative_to(Path(publish_report.REPORTS_DIR)))

    def test_current_report_validation_accepts_full_report(self):
        content = """
        <section class="finance-dashboard"></section>
        <h2>次交易日推估</h2>
        <p>方向機率</p>
        """
        publish_report.validate_current_report_html(
            content, "reports/daily/2026-06-30.html"
        )

    def test_current_report_validation_rejects_simple_report(self):
        content = "<html><body><h1>台股每日研究報告</h1></body></html>"
        with self.assertRaises(RuntimeError):
            publish_report.validate_current_report_html(
                content, "reports/daily/2026-06-30.html"
            )


if __name__ == "__main__":
    unittest.main()
