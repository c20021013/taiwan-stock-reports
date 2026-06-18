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


if __name__ == "__main__":
    unittest.main()
