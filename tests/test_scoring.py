import unittest
from datetime import date, datetime

import stock_report


class ScoringTests(unittest.TestCase):
    def test_calculate_metrics_for_rising_prices(self):
        history = [
            {"close": 100 + index, "Trading_Volume": 1000 + index * 10}
            for index in range(80)
        ]
        metrics = stock_report.calculate_metrics(history)
        self.assertGreater(metrics["ret20"], 0)
        self.assertGreater(metrics["ret60"], 0)
        self.assertGreater(metrics["latest"], metrics["ma20"])
        self.assertGreater(metrics["latest"], metrics["ma60"])

    def test_growth_stock_receives_higher_score_than_declining_stock(self):
        history_up = [
            {"close": 100 + index * 0.8, "Trading_Volume": 1_000_000}
            for index in range(90)
        ]
        history_down = [
            {"close": 180 - index * 0.8, "Trading_Volume": 1_000_000}
            for index in range(90)
        ]
        good = stock_report.Security(
            "1111",
            "成長公司",
            "TWSE",
            172,
            1,
            1_000_000,
            100_000_000,
            pe=24,
            pb=3,
            revenue_yoy=25,
            revenue_ytd_yoy=20,
        )
        weak = stock_report.Security(
            "2222",
            "衰退公司",
            "TWSE",
            108,
            -1,
            1_000_000,
            100_000_000,
            pe=55,
            pb=7,
            revenue_yoy=-20,
            revenue_ytd_yoy=-15,
        )
        good.metrics = stock_report.calculate_metrics(history_up)
        weak.metrics = stock_report.calculate_metrics(history_down)
        stock_report.score_security(good)
        stock_report.score_security(weak)
        self.assertGreater(good.score, weak.score)

    def test_reasons_use_business_catalysts_not_technical_signals(self):
        history = [
            {"close": 100 + index, "Trading_Volume": 1_000_000 + index * 10_000}
            for index in range(90)
        ]
        security = stock_report.Security(
            "3333",
            "事件公司",
            "TWSE",
            189,
            1,
            1_000_000,
            100_000_000,
            revenue_yoy=22,
            revenue_ytd_yoy=18,
            industry="半導體業",
            events=["與客戶簽訂長期供貨合約"],
        )
        security.metrics = stock_report.calculate_metrics(history)
        stock_report.score_security(security)
        reason_text = "；".join(security.reasons)

        self.assertIn("公司事件", reason_text)
        self.assertIn("營運事件", reason_text)
        self.assertIn("潛在催化劑", reason_text)
        self.assertNotIn("均線", reason_text)
        self.assertNotIn("近 20 日上漲", reason_text)
        self.assertNotIn("量能", reason_text)

    def test_technical_score_alone_cannot_be_investment_recommendation(self):
        history = [
            {"close": 100 + index, "Trading_Volume": 1_000_000}
            for index in range(90)
        ]
        security = stock_report.Security(
            "4444",
            "無事件公司",
            "TWSE",
            189,
            1,
            1_000_000,
            100_000_000,
            revenue_yoy=-2,
            revenue_ytd_yoy=1,
            industry="電子零組件業",
        )
        security.metrics = stock_report.calculate_metrics(history)
        stock_report.score_security(security)

        self.assertNotEqual(security.label, "建議投資")
        self.assertTrue(
            any("沒有可驗證" in reason for reason in security.reasons)
        )

    def test_material_event_classification(self):
        self.assertEqual(
            stock_report.material_event_type("公告取得大型訂單"),
            "positive",
        )
        self.assertEqual(
            stock_report.material_event_type("公告終止合約"),
            "risk",
        )

    def test_financial_revenue_reason_uses_industry_appropriate_drivers(self):
        security = stock_report.Security(
            "5555",
            "金融公司",
            "TWSE",
            50,
            1,
            1_000_000,
            100_000_000,
            revenue_yoy=30,
            industry="金融保險業",
        )
        reason = stock_report.monthly_revenue_reason(security)

        self.assertIn("利差", reason)
        self.assertNotIn("訂單與出貨", reason)

    def test_international_section_explains_taiwan_stock_impact(self):
        indicators = [
            stock_report.InternationalIndicator(
                "sox",
                "費城半導體指數",
                "^SOX",
                6800,
                "2026-06-16",
                change_1d=2.5,
                change_5d=4.0,
                unit="點",
            ),
            stock_report.InternationalIndicator(
                "us10y",
                "美國 10 年債殖利率",
                "^TNX",
                4.2,
                "2026-06-16",
                delta_1d=-0.06,
                delta_5d=-0.10,
                unit="%",
            ),
        ]
        section = "\n".join(stock_report.international_section(indicators))

        self.assertIn("國際情勢與台股影響", section)
        self.assertIn("半導體風險偏好改善", section)
        self.assertIn("殖利率下降", section)

    def test_market_health_section_includes_requested_fields(self):
        health = stock_report.MarketHealth(
            electronic_ratio=55.1,
            financial_ratio=20.2,
            traditional_ratio=24.7,
            up_count=700,
            down_count=500,
            flat_count=30,
            above_ma20_count=18,
            above_ma20_total=30,
            retail_mtx_sentiment="資料暫缺",
        )
        section = "\n".join(stock_report.market_health_section(health))

        self.assertIn("台股大盤與資金健康度", section)
        self.assertIn("電子股 55.1%", section)
        self.assertIn("上漲 700", section)
        self.assertIn("候選池 60.0%", section)

    def test_market_health_classifies_traditional_industries(self):
        securities = {
            "1101": stock_report.Security(
                "1101",
                "台泥",
                "TWSE",
                40,
                1,
                1_000_000,
                30_000_000,
                industry="水泥工業",
            ),
            "2330": stock_report.Security(
                "2330",
                "台積電",
                "TWSE",
                1000,
                1,
                1_000_000,
                60_000_000,
                industry="半導體業",
            ),
            "2881": stock_report.Security(
                "2881",
                "富邦金",
                "TWSE",
                80,
                1,
                1_000_000,
                10_000_000,
                industry="金融保險業",
            ),
        }

        health = stock_report.market_health(securities, [])

        self.assertAlmostEqual(health.traditional_ratio, 30.0)
        self.assertAlmostEqual(health.electronic_ratio, 60.0)
        self.assertAlmostEqual(health.financial_ratio, 10.0)

    def test_chip_section_includes_institutional_and_credit_data(self):
        context = stock_report.ChipContext(
            institutional_date="2026-06-16",
            institutional_total_net=12_300_000_000,
            institutional_nets={"Foreign_Investor": 5_000_000_000},
            futures_date="2026-06-16",
            foreign_tx_net_open_interest=-12000,
            foreign_tx_net_delta=1000,
            option_date="2026-06-16",
            foreign_option_net_amount=-2_500_000,
            margin_date="2026-06-16",
            margin_money_delta=3_000_000_000,
            short_sale_delta=-1200,
        )
        section = "\n".join(stock_report.chip_section(context))

        self.assertIn("籌碼與信用交易動態", section)
        self.assertIn("現貨合計買賣超 🔴 123.0 億", section)
        self.assertIn("外資台指期淨未平倉 🟢 -12,000 口", section)
        self.assertIn("融券餘額變動 -1,200 張", section)

    def test_executive_summary_includes_tldr_visual_cues(self):
        indicators = [
            stock_report.InternationalIndicator(
                "vix",
                "VIX 波動率指數",
                "^VIX",
                28.0,
                "2026-06-16",
                change_1d=8.5,
                change_5d=12.0,
                unit="點",
            )
        ]
        health = stock_report.MarketHealth(
            electronic_ratio=71.3,
            financial_ratio=2.6,
            traditional_ratio=26.1,
            up_count=300,
            down_count=900,
            flat_count=50,
        )
        chips = stock_report.ChipContext(
            institutional_nets={"Foreign_Investor": -5_000_000_000},
            foreign_tx_net_open_interest=-20_000,
            foreign_tx_net_delta=-3_000,
        )

        section = "\n".join(
            stock_report.executive_summary_section(indicators, health, chips)
        )

        self.assertIn("今日盤後速覽 (TL;DR)", section)
        self.assertIn("🟢 今日跌多漲少", section)
        self.assertIn("電子股 71.3%", section)
        self.assertIn("🟢 外資現貨賣超 50.0 億", section)
        self.assertIn("⚠️ VIX 28.00", section)

    def test_latest_group_rows_respects_report_date(self):
        rows = [
            {"date": "2026-06-16", "name": "old"},
            {"date": "2026-06-17", "name": "report-day"},
            {"date": "2026-06-18", "name": "future"},
        ]

        latest = stock_report.latest_group_rows(rows, date(2026, 6, 17))

        self.assertEqual([row["name"] for row in latest], ["report-day"])

    def test_etf_section_uses_etf_specific_columns(self):
        etf = stock_report.Security(
            "0050",
            "元大台灣50",
            "TWSE",
            200,
            1,
            1_000_000,
            100_000_000,
            is_etf=True,
        )
        etf.metrics = {"ret20": 25.0, "ret60": 45.0, "volatility20": 20.0}
        etf.score = 75.0
        etf.label = "建議投資"
        etf.reasons = ["AI 與半導體需求帶動大型權值股獲利預期"]
        etf.risks = ["折溢價過高時降低買進效率"]

        markdown = stock_report.build_markdown(
            "daily",
            [etf],
            datetime(2026, 6, 17, 8, 0, tzinfo=stock_report.TAIPEI_TZ),
            [],
            stock_report.MarketHealth(),
            stock_report.ChipContext(),
        )
        etf_section = markdown.split("## 建議投資 ETF 及原因", 1)[1].split(
            "## 投資建議詳情", 1
        )[0]

        self.assertIn("主要曝險/成分股主題", etf_section)
        self.assertIn("折溢價提醒", etf_section)
        self.assertIn("iNAV", etf_section)
        self.assertIn("**25.0%** / **45.0%**", etf_section)
        self.assertNotIn("月營收YoY", etf_section)
        self.assertNotIn("| PE |", etf_section)

        detail_section = markdown.split("## 投資建議詳情", 1)[1]
        self.assertIn("主要曝險", detail_section)
        self.assertIn("折溢價提醒", detail_section)
        self.assertNotIn("月營收年增", detail_section)
        self.assertNotIn("本益比", detail_section)

    def test_financial_table_header_uses_clear_net_value_label(self):
        financial = stock_report.Security(
            "2881",
            "富邦金",
            "TWSE",
            80,
            1,
            1_000_000,
            100_000_000,
            pb=1.2,
            industry="金融保險業",
        )
        financial.metrics = {"ret20": 2.0, "ret60": 3.0, "volatility20": 20.0}
        financial.score = 70.0
        financial.label = "建議投資"
        financial.reasons = ["利差擴大可能推升金融業獲利"]
        financial.risks = ["股債市場波動影響淨值"]

        markdown = stock_report.build_markdown(
            "daily",
            [financial],
            datetime(2026, 6, 17, 8, 0, tzinfo=stock_report.TAIPEI_TZ),
            [],
            stock_report.MarketHealth(),
            stock_report.ChipContext(),
        )

        self.assertIn(
            "| 代碼 | 名稱 | 分數 | 20D/60D報酬 | 累計EPS年增率 (YoY) | 股淨比 (PB) | 淨值變動 |",
            markdown,
        )
        self.assertIn("本益比 (PE)", markdown)
        self.assertIn("月營收年增率 (YoY)", markdown)
        self.assertNotIn("OCI／淨值變動", markdown)
        self.assertNotIn("QQ", markdown)

    def test_markdown_to_html_renders_bold_numbers(self):
        html = stock_report.markdown_to_html(
            "| 數字 |\n|---:|\n| **25.0%** |",
            "測試",
        )

        self.assertIn("<strong>25.0%</strong>", html)


if __name__ == "__main__":
    unittest.main()
