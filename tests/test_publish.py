import unittest
from pathlib import Path
from unittest.mock import patch

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
        <p>Next Trading Day</p>
        <h2>2026-07-03 推估</h2>
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

    def test_latest_alias_paths_include_global_and_mode_aliases(self):
        self.assertEqual(
            publish_report.latest_alias_paths("reports/daily/2026-07-06.html"),
            [
                "index.html",
                "reports/latest.html",
                "reports/daily/latest.html",
            ],
        )

    def test_latest_alias_paths_for_root_latest(self):
        self.assertEqual(
            publish_report.latest_alias_paths("reports/latest.html"),
            ["index.html", "reports/latest.html"],
        )

    def test_scheduled_github_action_can_publish(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "schedule",
        }
        with patch.dict(publish_report.os.environ, env, clear=True):
            allowed, reason = publish_report.publish_allowed()
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_manual_publish_requires_explicit_opt_in(self):
        with patch.dict(publish_report.os.environ, {}, clear=True):
            allowed, reason = publish_report.publish_allowed()
        self.assertFalse(allowed)
        self.assertIn("manual/local publish is disabled", reason)

    def test_manual_publish_opt_in_can_publish(self):
        with patch.dict(
            publish_report.os.environ,
            {"ALLOW_MANUAL_PUBLISH": "true"},
            clear=True,
        ):
            allowed, reason = publish_report.publish_allowed()
        self.assertTrue(allowed)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
