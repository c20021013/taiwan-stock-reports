#!/usr/bin/env python3
"""Generate explainable Taiwan stock and ETF research reports."""

from __future__ import annotations

import argparse
import http.client
import html
import json
import math
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
CACHE_DIR = ROOT / "cache"
CONFIG_PATH = ROOT / "config.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "TaiwanStockResearch/1.0"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.twse.com.tw/",
}
FETCH_RETRIES = 4
TAIPEI_TZ = timezone(timedelta(hours=8))

TWSE_DAILY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_VALUE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TWSE_MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_VALUE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TPEX_MATERIAL_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TDCC_HOLDING_URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
TAIFEX_INSTITUTIONAL_URL = (
    "https://openapi.taifex.com.tw/v1/"
    "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
)


@dataclass
class Security:
    symbol: str
    name: str
    market: str
    close: float
    change: float
    volume: float
    value: float
    pe: float | None = None
    pb: float | None = None
    yield_pct: float | None = None
    revenue_yoy: float | None = None
    revenue_ytd_yoy: float | None = None
    industry: str = ""
    is_etf: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float | None] = field(default_factory=dict)
    score: float = 0.0
    label: str = ""
    events: list[str] = field(default_factory=list)
    news_titles: list[str] = field(default_factory=list)
    eps_yoy: float | None = None
    eps_period: str = ""
    equity_change: float | None = None
    equity_change_pct: float | None = None
    equity_date: str = ""
    large_holder_pct: float | None = None
    large_holder_date: str = ""
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    data_date: str = ""


@dataclass
class InternationalIndicator:
    key: str
    name: str
    symbol: str
    latest: float
    latest_date: str
    change_1d: float | None = None
    change_5d: float | None = None
    delta_1d: float | None = None
    delta_5d: float | None = None
    unit: str = ""


@dataclass
class MarketHealth:
    data_date: str = ""
    electronic_ratio: float | None = None
    financial_ratio: float | None = None
    traditional_ratio: float | None = None
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    above_ma20_count: int = 0
    above_ma20_total: int = 0
    retail_mtx_sentiment: str = "資料暫缺：散戶小台指淨部位需穩定期交所明細或付費資料源"


@dataclass
class ChipContext:
    institutional_date: str = ""
    institutional_total_net: float | None = None
    institutional_nets: dict[str, float] = field(default_factory=dict)
    investment_trust_streaks: list[str] = field(default_factory=list)
    foreign_sell_streaks: list[str] = field(default_factory=list)
    futures_date: str = ""
    foreign_tx_net_open_interest: int | None = None
    foreign_tx_net_delta: int | None = None
    option_date: str = ""
    foreign_option_net_amount: float | None = None
    margin_date: str = ""
    margin_money_delta: float | None = None
    short_sale_delta: int | None = None
    maintenance_ratio: float | None = None
    maintenance_note: str = "資料暫缺：大盤融資維持率目前需付費或另接穩定來源"


@dataclass
class ForecastFactor:
    name: str
    contribution: float
    evidence: str
    data_date: str = ""


@dataclass
class NextSessionForecast:
    up_probability: float
    flat_probability: float
    down_probability: float
    label: str
    confidence: str
    score: float
    coverage: float
    factors: list[ForecastFactor] = field(default_factory=list)
    invalidation: str = ""


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_dirs() -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)


def _fetch_json_legacy(url: str, cache_name: str, max_age_minutes: int = 30) -> Any:
    ensure_dirs()
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age <= max_age_minutes * 60:
            return json.loads(cache_path.read_text(encoding="utf-8"))

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        # Some TWSE certificates fail OpenSSL strict mode while still passing
        # normal certificate and hostname verification.
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if cache_path.exists():
            print(f"警告：{url} 連線失敗，改用快取資料：{exc}", file=sys.stderr)
            return json.loads(cache_path.read_text(encoding="utf-8"))
        raise RuntimeError(f"無法取得資料：{url}: {exc}") from exc


def fetch_json(url: str, cache_name: str, max_age_minutes: int = 30) -> Any:
    ensure_dirs()
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age <= max_age_minutes * 60:
            return json.loads(cache_path.read_text(encoding="utf-8"))

    context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        # Some TWSE certificates fail OpenSSL strict mode while still passing
        # normal certificate and hostname verification.
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    last_exc: Exception | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        headers = dict(HTTP_HEADERS)
        if "tpex.org.tw" in urllib.parse.urlparse(url).netloc.lower():
            headers["Referer"] = "https://www.tpex.org.tw/"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=context
            ) as response:
                raw = response.read().decode("utf-8-sig").strip()
            if not raw:
                raise ValueError("empty response")
            data = json.loads(raw)
            cache_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return data
        except (
            http.client.HTTPException,
            urllib.error.URLError,
            ConnectionError,
            OSError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_exc = exc
            if attempt < FETCH_RETRIES:
                print(
                    f"Warning: fetch attempt {attempt}/{FETCH_RETRIES} failed "
                    f"for {url}: {exc}",
                    file=sys.stderr,
                )
                time.sleep(min(2 * attempt, 8))

    if cache_path.exists():
        print(
            f"Warning: using cached data for {url}; latest fetch failed: {last_exc}",
            file=sys.stderr,
        )
        return json.loads(cache_path.read_text(encoding="utf-8"))
    raise RuntimeError(f"Unable to fetch data: {url}: {last_exc}") from last_exc


def fetch_optional_json(
    url: str, cache_name: str, max_age_minutes: int = 30
) -> Any:
    try:
        return fetch_json(url, cache_name, max_age_minutes)
    except RuntimeError as exc:
        print(f"警告：輔助資料暫時無法取得，略過：{exc}", file=sys.stderr)
        return []


def fetch_yahoo_chart(symbol: str, cache_name: str) -> Any:
    quoted = urllib.parse.quote(symbol, safe="")
    url = f"{YAHOO_CHART_URL.format(symbol=quoted)}?range=1mo&interval=1d"
    return fetch_optional_json(url, cache_name, 60)


def finmind_data(
    dataset: str,
    start_date: date,
    cache_name: str,
    data_id: str = "",
    max_age_minutes: int = 360,
) -> list[dict[str, Any]]:
    query = {
        "dataset": dataset,
        "start_date": start_date.isoformat(),
    }
    if data_id:
        query["data_id"] = data_id
    token = os.environ.get("FINMIND_TOKEN", "")
    if token:
        query["token"] = token
    payload = fetch_optional_json(
        f"{FINMIND_URL}?{urllib.parse.urlencode(query)}",
        cache_name,
        max_age_minutes,
    )
    if isinstance(payload, dict) and payload.get("status") == 200:
        return payload.get("data", [])
    return []


def finmind_history(symbol: str, start_date: date, token: str = "") -> list[dict[str, Any]]:
    query = {
        "dataset": "TaiwanStockPrice",
        "data_id": symbol,
        "start_date": start_date.isoformat(),
    }
    if token:
        query["token"] = token
    url = f"{FINMIND_URL}?{urllib.parse.urlencode(query)}"
    payload = fetch_json(
        url,
        f"price_{symbol}_{start_date.isoformat()}.json",
        max_age_minutes=360,
    )
    if payload.get("status") != 200:
        return []
    return payload.get("data", [])


def number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("+", "").strip()
    if text in {"", "-", "--", "N/A", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def first_value(row: dict[str, Any], fragments: Iterable[str]) -> Any:
    for key, value in row.items():
        if all(fragment in key for fragment in fragments):
            return value
    return None


def latest_tpex_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max(str(row.get("Date", "")) for row in rows)
    return [row for row in rows if str(row.get("Date", "")) == latest]


def clean_text(value: Any, max_length: int = 100) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def material_event_type(subject: str, explanation: str = "") -> str | None:
    text = f"{subject} {explanation}"
    risk_keywords = (
        "虧損",
        "停工",
        "違約",
        "罰鍰",
        "裁罰",
        "訴訟",
        "下修",
        "減產",
        "終止合約",
        "訂單取消",
        "解約",
        "董事長異動",
        "總經理異動",
        "辭任",
        "減資",
        "退票",
        "事故",
        "火災",
    )
    positive_keywords = (
        "簽訂",
        "訂單",
        "得標",
        "藥證",
        "認證",
        "授權",
        "策略合作",
        "策略聯盟",
        "量產",
        "擴產",
        "產能擴充",
        "庫藏股",
        "處分利益",
        "獲利成長",
        "營收成長",
        "創新高",
        "轉盈",
        "現金股利",
        "股利分派",
    )
    if any(keyword in text for keyword in risk_keywords):
        return "risk"
    if any(keyword in text for keyword in positive_keywords):
        return "positive"
    return None


def attach_material_events(
    rows: list[dict[str, Any]], securities: dict[str, Security]
) -> None:
    for row in rows:
        symbol = str(
            row.get("SecuritiesCompanyCode")
            or first_value(row, ["公司", "代號"])
            or ""
        ).strip()
        security = securities.get(symbol)
        if not security:
            continue
        subject = clean_text(first_value(row, ["主旨"]))
        explanation = clean_text(first_value(row, ["說明"]), 300)
        event_type = material_event_type(subject, explanation)
        if event_type == "positive" and subject:
            if subject not in security.events:
                security.events.append(subject)
        elif event_type == "risk" and subject:
            risk = f"公司重大訊息：{subject}"
            if risk not in security.risks:
                security.risks.append(risk)


def industry_catalyst(security: Security) -> str:
    if security.is_etf:
        if security.symbol in {"0050", "006208"}:
            return "台灣大型權值股獲利成長、AI 供應鏈接單與外資回流若同步發生，可推升成分股評價"
        if security.symbol in {"0052", "00830", "00881", "00935"}:
            return "AI 晶片、先進製程與資料中心資本支出持續擴張，可帶動半導體與科技成分股獲利上修"
        if security.symbol in {"00646"}:
            return "美國大型企業獲利成長、通膨降溫與利率下降，可能推升美股整體評價"
        if security.symbol in {"00662", "00757"}:
            return "美國大型科技公司 AI 投資轉化為營收與獲利，是推升科技 ETF 的主要事件"
        if security.symbol in {"0056", "00878", "00919", "00929"}:
            return "成分股維持或提高配息、企業獲利改善與收益型資金流入，可支撐高股息 ETF"
        return "成分股企業獲利上修與資金持續流入，是 ETF 上漲的主要催化劑"

    industry = security.industry
    mappings = (
        (("半導體",), "AI／高效運算晶片、先進製程與封裝需求帶動客戶追加訂單，可促使市場上修獲利預估"),
        (("電腦及週邊", "資訊服務"), "雲端業者擴大 AI 伺服器與資料中心資本支出，可能帶動伺服器出貨與訂單成長"),
        (("電子零組件",), "AI 伺服器、高速傳輸與高階電子零組件需求增加，可能改善產品組合與毛利率"),
        (("電機機械", "電器電纜"), "台電強韌電網、再生能源併網與資料中心用電需求，可能推升重電設備訂單"),
        (("通信網路",), "資料中心高速網路、低軌衛星或電信升級訂單增加，可能帶動營收與獲利成長"),
        (("光電",), "資料中心光通訊、車用電子或高階顯示需求回升，可能帶動出貨與報價改善"),
        (("航運",), "運價上漲、港口壅塞或運力供給受限，可能直接改善航運公司的獲利預期"),
        (("金融",), "利差擴大、股債市場上漲帶來投資收益，或併購綜效落地，可能推升金融業獲利"),
        (("建材營造",), "建案完工交屋與入帳時點，可使營收及獲利明顯跳升，但須確認不是一次性認列"),
        (("生技醫療", "生技"), "新藥試驗結果、藥證核准、授權或里程碑金入帳，是可能推升評價的關鍵事件"),
        (("汽車",), "新車款放量、電動車零組件新增訂單或供應鏈市占提升，可能帶動營收與獲利上修"),
        (("觀光餐旅",), "來台旅客與住房率提升、展店效益或客單價成長，可能推升營收與獲利"),
    )
    for fragments, catalyst in mappings:
        if any(fragment in industry for fragment in fragments):
            return catalyst
    if industry:
        return f"{industry}需求回升、新訂單落地或產品報價改善，才會形成可持續的上漲催化劑"
    return "新訂單、獲利上修或可量化的公司重大訊息出現，才會形成較可信的上漲催化劑"


def monthly_revenue_reason(security: Security) -> str:
    yoy = security.revenue_yoy or 0.0
    industry = security.industry
    if "金融" in industry:
        return (
            f"營運事件：最新月營收年增 {yoy:.1f}%；利差、保險投資損益或"
            "資本市場收益改善可能推升獲利，仍需用月度獲利與法說確認來源"
        )
    if "建材營造" in industry:
        return (
            f"營運事件：最新月營收年增 {yoy:.1f}%；建案完工交屋與入帳"
            "可能推升當期獲利，關鍵是後續是否仍有可認列案量"
        )
    if "油電燃氣" in industry:
        return (
            f"營運事件：最新月營收年增 {yoy:.1f}%；售電量、電價與燃料價差、"
            "工程進度或轉投資收益若同步改善，市場可能上修獲利預估"
        )
    return (
        f"營運事件：最新月營收年增 {yoy:.1f}%；若成長來自訂單與出貨延續，"
        "市場可能上修獲利預估"
    )


def cumulative_revenue_reason(security: Security) -> str:
    yoy = security.revenue_ytd_yoy or 0.0
    if "金融" in security.industry:
        return (
            f"累計營收年增 {yoy:.1f}%，顯示收益改善不只集中在單月，"
            "但仍須核對一次性投資損益"
        )
    if "建材營造" in security.industry:
        return (
            f"累計營收年增 {yoy:.1f}%，反映本年度交屋認列增加，"
            "後續動能取決於完工與交屋時程"
        )
    return (
        f"累計營收年增 {yoy:.1f}%，顯示需求或出貨成長不只集中在單月"
    )


def is_financial_stock(security: Security) -> bool:
    return "金融" in security.industry


def is_electronic_industry(industry: str) -> bool:
    return any(
        keyword in industry
        for keyword in (
            "電子",
            "半導體",
            "電腦",
            "光電",
            "通信網路",
            "資訊服務",
        )
    )


def is_probable_fund_symbol(symbol: str) -> bool:
    return symbol.startswith("00")


def market_sector_bucket(security: Security) -> str | None:
    if (
        not security.symbol.isdigit()
        or len(security.symbol) != 4
        or security.is_etf
        or is_probable_fund_symbol(security.symbol)
        or security.value <= 0
    ):
        return None
    if is_financial_stock(security):
        return "financial"
    if is_electronic_industry(security.industry):
        return "electronic"
    return "traditional"


def market_health(
    securities: dict[str, Security], candidates: list[Security]
) -> MarketHealth:
    sector_values = {"electronic": 0.0, "financial": 0.0, "traditional": 0.0}
    total_value = 0.0
    up_count = down_count = flat_count = 0

    for security in securities.values():
        bucket = market_sector_bucket(security)
        if bucket is None:
            continue
        total_value += security.value
        sector_values[bucket] += security.value

        if security.change > 0:
            up_count += 1
        elif security.change < 0:
            down_count += 1
        else:
            flat_count += 1

    stock_candidates = [
        item
        for item in candidates
        if not item.is_etf and item.metrics.get("ma20") and item.metrics.get("latest")
    ]
    above_count = sum(
        1
        for item in stock_candidates
        if (item.metrics.get("latest") or 0) > (item.metrics.get("ma20") or 0)
    )

    ratio = lambda value: (value / total_value * 100) if total_value else None
    market_dates = [item.data_date for item in securities.values() if item.data_date]
    return MarketHealth(
        data_date=max(market_dates, default=""),
        electronic_ratio=ratio(sector_values["electronic"]),
        financial_ratio=ratio(sector_values["financial"]),
        traditional_ratio=ratio(sector_values["traditional"]),
        up_count=up_count,
        down_count=down_count,
        flat_count=flat_count,
        above_ma20_count=above_count,
        above_ma20_total=len(stock_candidates),
    )


def latest_taifex_institutional_rows(
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    rows = fetch_optional_json(
        TAIFEX_INSTITUTIONAL_URL,
        "taifex_institutional_latest.json",
        60,
    )
    return latest_group_rows(rows if isinstance(rows, list) else [], as_of)


def load_non_institution_mtx_sentiment(as_of: date | None = None) -> str:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    rows = [
        row
        for row in latest_taifex_institutional_rows(report_date)
        if str(row.get("ContractCode", "")).strip() == "小型臺指期貨"
    ]
    if not rows:
        return "資料暫缺：期交所小型臺指期貨三大法人資料尚未更新"
    institutional_net = sum(
        int(number(row.get("OpenInterest(Net)")) or 0) for row in rows
    )
    residual_net = -institutional_net
    latest_date = parse_row_date(rows[0])
    direction = "淨多" if residual_net > 0 else "淨空" if residual_net < 0 else "持平"
    marker = direction_marker(residual_net)
    return (
        f"{latest_date.isoformat() if latest_date else '—'} 非三大法人小台指{direction} "
        f"{marker} {abs(residual_net):,} 口"
        "（由期交所三大法人未平倉反推，包含自然人與其他法人，非純散戶）"
    )


def parse_row_date(row: dict[str, Any]) -> date | None:
    value = (
        row.get("date")
        or row.get("Date")
        or row.get("資料日期")
        or row.get("\ufeff資料日期")
    )
    if not value:
        return None
    text = str(value).strip()[:10]
    if re.fullmatch(r"\d{7}", text):
        roc_year = int(text[:3])
        text = f"{roc_year + 1911:04d}-{text[3:5]}-{text[5:]}"
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def quarter_label(value: str) -> str:
    parsed = parse_row_date({"date": value})
    if not parsed:
        return value
    return f"{parsed.year}Q{(parsed.month - 1) // 3 + 1}"


def rows_on_or_before(
    rows: list[dict[str, Any]], as_of: date | None = None
) -> list[dict[str, Any]]:
    if as_of is None:
        return rows
    return [
        row
        for row in rows
        if (row_date := parse_row_date(row)) is not None and row_date <= as_of
    ]


def latest_group_rows(
    rows: list[dict[str, Any]], as_of: date | None = None
) -> list[dict[str, Any]]:
    if not rows:
        return []
    dated_rows = [
        (row_date, row)
        for row in rows
        if (row_date := parse_row_date(row)) is not None
        and (as_of is None or row_date <= as_of)
    ]
    if not dated_rows:
        return []
    latest = max(row_date for row_date, _ in dated_rows)
    return [row for row_date, row in dated_rows if row_date == latest]


def fmt_money_yi(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 100_000_000:.1f} 億"


def fmt_thousand_amount_yi(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 100_000:.1f} 億"


def fmt_signed_int(value: int | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,}{suffix}"


def direction_marker(value: float | int | None) -> str:
    if value is None:
        return "⚪"
    if value > 0:
        return "🔴"
    if value < 0:
        return "🟢"
    return "⚪"


def marked_money_yi(value: float | None) -> str:
    return f"{direction_marker(value)} {fmt_money_yi(value)}"


def marked_signed_int(value: int | None, suffix: str = "") -> str:
    return f"{direction_marker(value)} {fmt_signed_int(value, suffix)}"


def strong_if(text: str, condition: bool) -> str:
    return f"**{text}**" if condition else text


def investor_net(row: dict[str, Any], prefix: str) -> float:
    return float(row.get(f"{prefix}_buy", 0) or 0) - float(
        row.get(f"{prefix}_sell", 0) or 0
    )


def consecutive_streak(
    rows: list[dict[str, Any]], prefix: str, direction: str
) -> tuple[int, float, str]:
    ordered = sorted(rows, key=lambda row: str(row.get("date", "")), reverse=True)
    if not ordered:
        return 0, 0.0, ""
    latest_date = str(ordered[0].get("date", ""))
    streak = 0
    latest_net = investor_net(ordered[0], prefix)
    for row in ordered:
        net = investor_net(row, prefix)
        if direction == "buy" and net > 0:
            streak += 1
        elif direction == "sell" and net < 0:
            streak += 1
        else:
            break
    return streak, latest_net, latest_date


def load_candidate_institutional_rows(
    symbol: str, as_of: date | None = None
) -> list[dict[str, Any]]:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    start = report_date - timedelta(days=21)
    rows = finmind_data(
        "TaiwanStockInstitutionalInvestorsBuySellWide",
        start,
        f"institutional_wide_{symbol}_{start.isoformat()}.json",
        data_id=symbol,
        max_age_minutes=720,
    )
    return rows_on_or_before(rows, report_date)


def load_stock_news(symbol: str, as_of: date | None = None) -> list[str]:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    start = report_date - timedelta(days=7)
    rows = finmind_data(
        "TaiwanStockNews",
        start,
        f"news_{symbol}_{start.isoformat()}.json",
        data_id=symbol,
        max_age_minutes=720,
    )
    rows = rows_on_or_before(rows, report_date)
    titles: list[str] = []
    for row in sorted(rows, key=lambda item: str(item.get("date", "")), reverse=True):
        source = clean_text(row.get("source"), 30).lower()
        raw_title = clean_text(row.get("title"), 120)
        if source in {"cmoney", "cmoney投資網誌"}:
            continue
        if any(
            term in raw_title
            for term in (
                "股市爆料同學會",
                "討論牆 | 盤中速報",
                "未來操作指南",
                "技術分析分享",
            )
        ):
            continue
        title = clean_text(raw_title, 46)
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 2:
            break
    return titles


def attach_news_and_financials(
    candidates: list[Security], as_of: date | None = None
) -> None:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    stocks = sorted(
        [item for item in candidates if not item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    for security in stocks[:12]:
        security.news_titles = load_stock_news(security.symbol, report_date)

    for security in [item for item in stocks if is_financial_stock(item)][:12]:
        attach_financial_metrics(security, report_date)


def statement_values_by_date(
    rows: list[dict[str, Any]], as_of: date
) -> dict[str, dict[str, float]]:
    by_date: dict[str, dict[str, float]] = {}
    for row in rows_on_or_before(rows, as_of):
        parsed_date = parse_row_date(row)
        value = number(row.get("value"))
        item_type = str(row.get("type", ""))
        if not parsed_date or value is None or not item_type:
            continue
        by_date.setdefault(parsed_date.isoformat(), {})[item_type] = value
    return by_date


def cumulative_eps_metrics(
    by_date: dict[str, dict[str, float]]
) -> tuple[float | None, str]:
    eps_dates = sorted(
        row_date for row_date, values in by_date.items() if "EPS" in values
    )
    if not eps_dates:
        return None, ""
    latest = date.fromisoformat(eps_dates[-1])
    current_dates = [
        row_date
        for row_date in eps_dates
        if date.fromisoformat(row_date).year == latest.year
        and date.fromisoformat(row_date) <= latest
    ]
    previous_dates = [
        f"{latest.year - 1}{row_date[4:]}" for row_date in current_dates
    ]
    if not current_dates or any(
        row_date not in by_date or "EPS" not in by_date[row_date]
        for row_date in previous_dates
    ):
        return None, quarter_label(latest.isoformat())
    current_total = sum(by_date[row_date]["EPS"] for row_date in current_dates)
    previous_total = sum(by_date[row_date]["EPS"] for row_date in previous_dates)
    if previous_total == 0:
        return None, quarter_label(latest.isoformat())
    return pct_change(current_total, previous_total), quarter_label(latest.isoformat())


def equity_change_metrics(
    by_date: dict[str, dict[str, float]]
) -> tuple[float | None, float | None, str]:
    equity_dates = sorted(
        row_date for row_date, values in by_date.items() if "Equity" in values
    )
    if len(equity_dates) < 2:
        return None, None, quarter_label(equity_dates[-1]) if equity_dates else ""
    previous, latest = equity_dates[-2:]
    previous_value = by_date[previous]["Equity"]
    latest_value = by_date[latest]["Equity"]
    change = latest_value - previous_value
    change_pct = pct_change(latest_value, previous_value) if previous_value else None
    return change, change_pct, quarter_label(latest)


def attach_financial_metrics(
    security: Security, as_of: date | None = None
) -> None:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    start = report_date - timedelta(days=900)
    income_rows = finmind_data(
        "TaiwanStockFinancialStatements",
        start,
        f"financials_{security.symbol}_{start.isoformat()}.json",
        data_id=security.symbol,
        max_age_minutes=1440,
    )
    income_by_date = statement_values_by_date(income_rows, report_date)
    security.eps_yoy, security.eps_period = cumulative_eps_metrics(income_by_date)

    balance_rows = finmind_data(
        "TaiwanStockBalanceSheet",
        start,
        f"balance_{security.symbol}_{start.isoformat()}.json",
        data_id=security.symbol,
        max_age_minutes=1440,
    )
    balance_by_date = statement_values_by_date(balance_rows, report_date)
    (
        security.equity_change,
        security.equity_change_pct,
        security.equity_date,
    ) = equity_change_metrics(balance_by_date)


def attach_tdcc_holdings(
    candidates: list[Security], as_of: date | None = None
) -> None:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    rows = fetch_optional_json(TDCC_HOLDING_URL, "tdcc_holding_levels.json", 720)
    eligible = rows_on_or_before(rows if isinstance(rows, list) else [], report_date)
    dated_rows = [(parse_row_date(row), row) for row in eligible]
    valid_dates = [row_date for row_date, _ in dated_rows if row_date]
    if not valid_dates:
        return
    latest_date = max(valid_dates)
    by_symbol = {item.symbol: item for item in candidates if not item.is_etf}
    for row_date, row in dated_rows:
        if row_date != latest_date or str(row.get("持股分級", "")).strip() != "15":
            continue
        symbol = str(row.get("證券代號", "")).strip()
        security = by_symbol.get(symbol)
        large_holder_pct = number(row.get("占集保庫存數比例%"))
        if security and large_holder_pct is not None:
            security.large_holder_pct = large_holder_pct
            security.large_holder_date = latest_date.isoformat()


def load_chip_context(
    candidates: list[Security], as_of: date | None = None
) -> ChipContext:
    context = ChipContext()
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    start = report_date - timedelta(days=21)

    total_rows = finmind_data(
        "TaiwanStockTotalInstitutionalInvestors",
        start,
        f"total_institutional_{start.isoformat()}.json",
        max_age_minutes=720,
    )
    latest_total = latest_group_rows(total_rows, report_date)
    if latest_total:
        context.institutional_date = str(latest_total[0].get("date", ""))
        for row in latest_total:
            name = str(row.get("name", ""))
            net = float(row.get("buy", 0) or 0) - float(row.get("sell", 0) or 0)
            if name.lower() == "total":
                context.institutional_total_net = net
                continue
            context.institutional_nets[name] = net
        if context.institutional_total_net is None:
            context.institutional_total_net = sum(context.institutional_nets.values())

    streak_candidates = sorted(
        [item for item in candidates if not item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )[:24]
    trust_items: list[tuple[int, float, str]] = []
    foreign_items: list[tuple[int, float, str]] = []
    for security in streak_candidates:
        rows = load_candidate_institutional_rows(security.symbol, report_date)
        trust_streak, trust_net, _ = consecutive_streak(
            rows, "Investment_Trust", "buy"
        )
        foreign_streak, foreign_net, _ = consecutive_streak(
            rows, "Foreign_Investor", "sell"
        )
        if trust_streak:
            trust_items.append((trust_streak, trust_net, f"{security.symbol} {security.name}"))
        if foreign_streak:
            foreign_items.append((foreign_streak, foreign_net, f"{security.symbol} {security.name}"))
    trust_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
    foreign_items.sort(key=lambda item: (item[0], abs(item[1])), reverse=True)
    context.investment_trust_streaks = [
        f"{name}：連買 {streak} 日，最新 {net / 1000:.0f} 張"
        for streak, net, name in trust_items[:3]
    ]
    context.foreign_sell_streaks = [
        f"{name}：連賣 {streak} 日，最新 {abs(net) / 1000:.0f} 張"
        for streak, net, name in foreign_items[:3]
    ]

    futures_rows = finmind_data(
        "TaiwanFuturesInstitutionalInvestors",
        start,
        f"tx_futures_institutional_{start.isoformat()}.json",
        data_id="TX",
        max_age_minutes=720,
    )
    foreign_futures = [
        row for row in futures_rows if row.get("institutional_investors") == "外資"
    ]
    foreign_futures = rows_on_or_before(foreign_futures, report_date)
    foreign_futures.sort(key=lambda row: str(row.get("date", "")))
    if foreign_futures:
        latest = foreign_futures[-1]
        previous = foreign_futures[-2] if len(foreign_futures) >= 2 else None
        latest_net = int(latest.get("long_open_interest_balance_volume", 0) or 0) - int(
            latest.get("short_open_interest_balance_volume", 0) or 0
        )
        context.futures_date = str(latest.get("date", ""))
        context.foreign_tx_net_open_interest = latest_net
        if previous:
            previous_net = int(previous.get("long_open_interest_balance_volume", 0) or 0) - int(
                previous.get("short_open_interest_balance_volume", 0) or 0
            )
            context.foreign_tx_net_delta = latest_net - previous_net

    official_tx_rows = [
        row
        for row in latest_taifex_institutional_rows(report_date)
        if str(row.get("ContractCode", "")).strip() == "臺股期貨"
        and str(row.get("Item", "")).strip() == "外資及陸資"
    ]
    if official_tx_rows:
        official_row = official_tx_rows[0]
        official_date = parse_row_date(official_row)
        official_net = int(number(official_row.get("OpenInterest(Net)")) or 0)
        existing_date = parse_row_date({"date": context.futures_date})
        if official_date and (existing_date is None or official_date >= existing_date):
            previous_net = context.foreign_tx_net_open_interest
            if existing_date and official_date > existing_date and previous_net is not None:
                context.foreign_tx_net_delta = official_net - previous_net
            context.futures_date = official_date.isoformat()
            context.foreign_tx_net_open_interest = official_net

    option_rows = finmind_data(
        "TaiwanOptionInstitutionalInvestors",
        start,
        f"txo_option_institutional_{start.isoformat()}.json",
        data_id="TXO",
        max_age_minutes=720,
    )
    latest_options = [
        row
        for row in latest_group_rows(option_rows, report_date)
        if row.get("institutional_investors") == "外資"
    ]
    if latest_options:
        context.option_date = str(latest_options[0].get("date", ""))
        bullish = bearish = 0.0
        for row in latest_options:
            call_put = row.get("call_put")
            long_amount = float(row.get("long_open_interest_balance_amount", 0) or 0)
            short_amount = float(row.get("short_open_interest_balance_amount", 0) or 0)
            if call_put == "買權":
                bullish += long_amount
                bearish += short_amount
            else:
                bearish += long_amount
                bullish += short_amount
        context.foreign_option_net_amount = bullish - bearish

    margin_rows = finmind_data(
        "TaiwanStockTotalMarginPurchaseShortSale",
        start,
        f"total_margin_{start.isoformat()}.json",
        max_age_minutes=720,
    )
    latest_margin = latest_group_rows(margin_rows, report_date)
    if latest_margin:
        context.margin_date = str(latest_margin[0].get("date", ""))
        by_name = {row.get("name"): row for row in latest_margin}
        money = by_name.get("MarginPurchaseMoney")
        short = by_name.get("ShortSale")
        if money:
            context.margin_money_delta = float(money.get("TodayBalance", 0) or 0) - float(
                money.get("YesBalance", 0) or 0
            )
        if short:
            context.short_sale_delta = int(short.get("TodayBalance", 0) or 0) - int(
                short.get("YesBalance", 0) or 0
            )
    return context


def load_market_data(config: dict[str, Any]) -> dict[str, Security]:
    twse_daily = fetch_json(TWSE_DAILY_URL, "twse_daily.json")
    tpex_daily = latest_tpex_rows(fetch_json(TPEX_DAILY_URL, "tpex_daily.json"))
    twse_values = fetch_json(TWSE_VALUE_URL, "twse_values.json")
    tpex_values = fetch_json(TPEX_VALUE_URL, "tpex_values.json")
    twse_revenue = fetch_json(TWSE_REVENUE_URL, "twse_revenue.json", 720)
    tpex_revenue = fetch_json(TPEX_REVENUE_URL, "tpex_revenue.json", 720)
    twse_material = fetch_optional_json(
        TWSE_MATERIAL_URL, "twse_material.json", 30
    )
    tpex_material = fetch_optional_json(
        TPEX_MATERIAL_URL, "tpex_material.json", 30
    )

    securities: dict[str, Security] = {}
    etf_symbols = set(config["etf_symbols"])

    for row in twse_daily:
        symbol = str(row.get("Code", "")).strip()
        close = number(row.get("ClosingPrice"))
        if not symbol or close is None:
            continue
        securities[symbol] = Security(
            symbol=symbol,
            name=str(row.get("Name", symbol)).strip(),
            market="TWSE",
            close=close,
            change=number(row.get("Change")) or 0.0,
            volume=number(row.get("TradeVolume")) or 0.0,
            value=number(row.get("TradeValue")) or 0.0,
            is_etf=symbol in etf_symbols,
            data_date=(
                parsed_date.isoformat()
                if (parsed_date := parse_row_date(row))
                else ""
            ),
        )

    for row in tpex_daily:
        symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
        close = number(row.get("Close"))
        if not symbol or close is None:
            continue
        securities[symbol] = Security(
            symbol=symbol,
            name=str(row.get("CompanyName", symbol)).strip(),
            market="TPEx",
            close=close,
            change=number(row.get("Change")) or 0.0,
            volume=number(row.get("TradingShares")) or 0.0,
            value=number(row.get("TransactionAmount")) or 0.0,
            is_etf=symbol in etf_symbols,
            data_date=(
                parsed_date.isoformat()
                if (parsed_date := parse_row_date(row))
                else ""
            ),
        )

    for row in twse_values:
        security = securities.get(str(row.get("Code", "")).strip())
        if security:
            security.pe = number(row.get("PEratio"))
            security.pb = number(row.get("PBratio"))
            security.yield_pct = number(row.get("DividendYield"))

    for row in tpex_values:
        security = securities.get(str(row.get("SecuritiesCompanyCode", "")).strip())
        if security:
            security.pe = number(row.get("PriceEarningRatio"))
            security.pb = number(row.get("PriceBookRatio"))
            security.yield_pct = number(row.get("YieldRatio"))

    for row in [*twse_revenue, *tpex_revenue]:
        symbol = str(first_value(row, ["公司", "代號"]) or "").strip()
        security = securities.get(symbol)
        if not security:
            continue
        security.industry = str(first_value(row, ["產業"]) or "").strip()
        security.revenue_yoy = number(
            first_value(row, ["去年", "同月", "增減"])
        )
        security.revenue_ytd_yoy = number(
            first_value(row, ["累計", "前期", "增減"])
        )

    attach_material_events([*twse_material, *tpex_material], securities)
    return securities


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new / old - 1.0) * 100.0


def market_indicator(
    key: str,
    name: str,
    symbol: str,
    unit: str = "",
    as_of: date | None = None,
) -> InternationalIndicator | None:
    safe_name = symbol.replace("^", "idx_").replace("=", "_").replace(".", "_")
    payload = fetch_yahoo_chart(symbol, f"macro_{safe_name}.json")
    result = (
        payload.get("chart", {}).get("result", [None])[0]
        if isinstance(payload, dict)
        else None
    )
    if not result:
        return None
    timestamps = result.get("timestamp") or []
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    points = [
        (
            datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
            float(close),
        )
        for timestamp, close in zip(timestamps, closes)
        if close is not None
    ]
    if as_of is not None:
        points = [
            (point_date, close)
            for point_date, close in points
            if date.fromisoformat(point_date) <= as_of
        ]
    if len(points) < 2:
        return None

    latest_date, latest = points[-1]
    previous = points[-2][1]
    five_day_base = points[-6][1] if len(points) >= 6 else points[0][1]
    return InternationalIndicator(
        key=key,
        name=name,
        symbol=symbol,
        latest=latest,
        latest_date=latest_date,
        change_1d=pct_change(latest, previous),
        change_5d=pct_change(latest, five_day_base),
        delta_1d=latest - previous,
        delta_5d=latest - five_day_base,
        unit=unit,
    )


def load_international_context(
    as_of: date | None = None,
) -> list[InternationalIndicator]:
    definitions = [
        ("sp500", "S&P 500", "^GSPC", "點"),
        ("nasdaq", "Nasdaq Composite", "^IXIC", "點"),
        ("sox", "費城半導體指數", "^SOX", "點"),
        ("us10y", "美國 10 年債殖利率", "^TNX", "%"),
        ("dxy", "美元指數", "DX-Y.NYB", "點"),
        ("usdtwd", "美元／台幣", "USDTWD=X", "元"),
        ("vix", "VIX 波動率指數", "^VIX", "點"),
        ("wti", "WTI 原油", "CL=F", "美元"),
        ("gold", "黃金", "GC=F", "美元"),
    ]
    indicators: list[InternationalIndicator] = []
    for definition in definitions:
        indicator = market_indicator(*definition, as_of=as_of)
        if indicator:
            indicators.append(indicator)
    return indicators


def indicator_map(
    indicators: list[InternationalIndicator],
) -> dict[str, InternationalIndicator]:
    return {indicator.key: indicator for indicator in indicators}


def move_score(change_1d: float | None, change_5d: float | None) -> float:
    return (change_1d or 0.0) + (change_5d or 0.0) * 0.35


def international_bias(indicators: list[InternationalIndicator]) -> tuple[str, float]:
    data = indicator_map(indicators)
    score = 0.0
    for key, weight in (("sp500", 0.6), ("nasdaq", 0.8), ("sox", 1.2)):
        indicator = data.get(key)
        if indicator:
            score += clamp(move_score(indicator.change_1d, indicator.change_5d), -3, 3) * weight

    us10y = data.get("us10y")
    if us10y and us10y.delta_1d is not None:
        score -= clamp(us10y.delta_1d * 100 / 8, -2, 2)

    dxy = data.get("dxy")
    if dxy:
        score -= clamp(move_score(dxy.change_1d, dxy.change_5d) / 1.2, -2, 2)

    wti = data.get("wti")
    if wti:
        score -= clamp(move_score(wti.change_1d, wti.change_5d) / 4, -1.5, 1.5)

    vix = data.get("vix")
    if vix:
        score -= clamp(move_score(vix.change_1d, vix.change_5d) / 6, -2, 2)
        if vix.latest >= 25:
            score -= 1.0

    if score >= 2.0:
        return "偏多", score
    if score <= -2.0:
        return "偏空", score
    return "中性", score


def rounded_probabilities(
    up: float,
    flat: float,
    down: float,
) -> tuple[float, float, float]:
    values = {
        "up": round(up * 100, 1),
        "flat": round(flat * 100, 1),
        "down": round(down * 100, 1),
    }
    difference = round(100.0 - sum(values.values()), 1)
    largest = max(values, key=values.get)
    values[largest] = round(values[largest] + difference, 1)
    return values["up"], values["flat"], values["down"]


def cap_probability_distribution(
    probabilities: list[float],
    maximum: float = 0.60,
) -> list[float]:
    capped = list(probabilities)
    largest_index = max(range(len(capped)), key=capped.__getitem__)
    if capped[largest_index] <= maximum:
        return capped
    excess = capped[largest_index] - maximum
    capped[largest_index] = maximum
    other_indices = [index for index in range(len(capped)) if index != largest_index]
    other_total = sum(capped[index] for index in other_indices)
    if other_total <= 0:
        for index in other_indices:
            capped[index] += excess / len(other_indices)
    else:
        for index in other_indices:
            capped[index] += excess * capped[index] / other_total
    return capped


def neutral_next_session_forecast() -> NextSessionForecast:
    return NextSessionForecast(
        up_probability=31.7,
        flat_probability=36.6,
        down_probability=31.7,
        label="平盤",
        confidence="低",
        score=0.0,
        coverage=0.0,
        factors=[
            ForecastFactor(
                "資料完整度",
                0.0,
                "可用市場因子不足，機率已主動拉回接近均等分布",
            )
        ],
        invalidation="任何開盤前重大政策、地緣政治或權值股事件都可能改變方向。",
    )


def estimate_next_session(
    indicators: list[InternationalIndicator],
    health: MarketHealth,
    chips: ChipContext,
    as_of: date | None = None,
) -> NextSessionForecast:
    factors: list[ForecastFactor] = []
    data = indicator_map(indicators)

    if indicators:
        bias, raw_score = international_bias(indicators)
        contribution = clamp(raw_score / 4.0, -1.5, 1.5)
        latest_date = max(
            (item.latest_date for item in indicators if item.latest_date),
            default="",
        )
        factors.append(
            ForecastFactor(
                "國際風險偏好",
                contribution,
                f"美股、半導體、利率與波動率綜合為{bias}，原始分數 {raw_score:.1f}",
                latest_date,
            )
        )

    breadth_directional = health.up_count + health.down_count
    if breadth_directional:
        breadth_ratio = (
            health.up_count - health.down_count
        ) / breadth_directional
        contribution = clamp(breadth_ratio * 1.5, -1.2, 1.2)
        factors.append(
            ForecastFactor(
                "台股市場廣度",
                contribution,
                f"上漲 {health.up_count:,} 家、下跌 {health.down_count:,} 家",
                health.data_date,
            )
        )

    foreign_cash = chips.institutional_nets.get("Foreign_Investor")
    if foreign_cash is not None:
        contribution = clamp(foreign_cash / 30_000_000_000, -1.0, 1.0)
        action = "買超" if foreign_cash > 0 else "賣超" if foreign_cash < 0 else "持平"
        factors.append(
            ForecastFactor(
                "外資現貨",
                contribution,
                f"外資{action} {fmt_money_yi(abs(foreign_cash))}",
                chips.institutional_date,
            )
        )

    futures_net = chips.foreign_tx_net_open_interest
    futures_delta = chips.foreign_tx_net_delta
    if futures_net is not None or futures_delta is not None:
        position_score = (
            clamp((futures_net or 0) / 20_000, -1.0, 1.0) * 0.5
        )
        change_score = (
            clamp((futures_delta or 0) / 5_000, -1.0, 1.0) * 0.7
        )
        contribution = clamp(position_score + change_score, -1.2, 1.2)
        factors.append(
            ForecastFactor(
                "外資台指期",
                contribution,
                f"淨未平倉 {fmt_signed_int(futures_net, ' 口')}，"
                f"較前期 {fmt_signed_int(futures_delta, ' 口')}",
                chips.futures_date,
            )
        )

    usdtwd = data.get("usdtwd")
    if usdtwd and usdtwd.change_1d is not None:
        contribution = clamp(-usdtwd.change_1d / 0.75, -0.6, 0.6)
        factors.append(
            ForecastFactor(
                "美元／台幣",
                contribution,
                f"1 日變化 {fmt_change(usdtwd.change_1d)}；"
                "台幣升值通常較有利外資風險承擔，貶值則相反",
                usdtwd.latest_date,
            )
        )

    if not factors:
        return neutral_next_session_forecast()

    score = clamp(sum(item.contribution for item in factors), -4.0, 4.0)
    vix = data.get("vix")
    flat_logit = 0.9 - abs(score) * 0.25
    if vix:
        if vix.latest >= 25:
            flat_logit -= 0.25
        elif vix.latest <= 18:
            flat_logit += 0.15

    temperature = 2.2
    logits = [score / temperature, flat_logit / temperature, -score / temperature]
    maximum = max(logits)
    weights = [math.exp(value - maximum) for value in logits]
    total = sum(weights)
    raw = [value / total for value in weights]

    coverage = min(1.0, len(factors) / 5.0)
    evidence_weight = 0.35 + coverage * 0.45
    blended = [
        value * evidence_weight + (1 / 3) * (1 - evidence_weight)
        for value in raw
    ]
    blended = cap_probability_distribution(blended)
    up_probability, flat_probability, down_probability = rounded_probabilities(
        blended[0],
        blended[1],
        blended[2],
    )
    probabilities = {
        "上漲": up_probability,
        "平盤": flat_probability,
        "下跌": down_probability,
    }
    label = max(probabilities, key=probabilities.get)
    ranked = sorted(probabilities.values(), reverse=True)
    edge = ranked[0] - ranked[1]
    confidence = (
        "中等"
        if coverage >= 0.8 and edge >= 10
        else "偏低"
        if coverage >= 0.6 and edge >= 5
        else "低"
    )

    if label == "上漲":
        invalidation = (
            "若開盤前美股期貨明顯轉弱、台幣快速貶值，或外資期現貨轉為擴大賣超，"
            "偏多情境失效。"
        )
    elif label == "下跌":
        invalidation = (
            "若開盤前美股與半導體期貨明顯轉強、台幣升值，或外資期貨空單快速回補，"
            "偏空情境失效。"
        )
    else:
        invalidation = (
            "若開盤前美股期貨、匯率或重大權值股事件造成超過一般波動的單向衝擊，"
            "平盤情境失效。"
        )

    return NextSessionForecast(
        up_probability=up_probability,
        flat_probability=flat_probability,
        down_probability=down_probability,
        label=label,
        confidence=confidence,
        score=round(score, 2),
        coverage=coverage,
        factors=sorted(factors, key=lambda item: abs(item.contribution), reverse=True),
        invalidation=invalidation,
    )


def next_session_forecast_section(
    forecast: NextSessionForecast,
) -> list[str]:
    marker = {"上漲": "🔴", "平盤": "⚪", "下跌": "🟢"}[forecast.label]
    factor_lines = [
        f"- {direction_marker(item.contribution)} {item.name}：{item.evidence}"
        f"{f'（資料日 {item.data_date}）' if item.data_date else ''}；"
        f"模型貢獻 {item.contribution:+.2f}。"
        for item in forecast.factors[:5]
    ]
    return [
        "## 次一交易日大盤方向推估",
        "",
        "> 預測對象為臺灣加權股價指數次一交易日收盤相對前一交易日收盤；"
        "上漲與下跌門檻為 ±0.3%，介於其間定義為平盤。",
        "",
        f"- 目前猜測：{marker} **{forecast.label}**；可信度："
        f"**{forecast.confidence}**；有效因子覆蓋率 {forecast.coverage * 100:.0f}%。",
        f"- 機率分布：🔴 上漲 {forecast.up_probability:.1f}%｜"
        f"⚪ 平盤 {forecast.flat_probability:.1f}%｜"
        f"🟢 下跌 {forecast.down_probability:.1f}%（合計 100.0%）。",
        f"- 綜合情境分數：{forecast.score:+.2f}。正值偏多、負值偏空；"
        "分數只用於機率換算，不代表預期漲跌幅。",
        "",
        "### 主要推估依據",
        "",
        *factor_lines,
        "",
        f"- 反證條件：{forecast.invalidation}",
        "- 這是規則型情境推估，不是報酬保證；突發政策、戰事、天災或公司重大訊息"
        "可能使隔日走勢完全不同。單一方向機率上限為 60%，並應以每日累積命中紀錄"
        "校準，不因單次猜對就提高信任。",
        "",
    ]


def fmt_change(value: float | None) -> str:
    return fmt(value, "%") if value is not None else "—"


def fmt_delta_bps(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f} bp"


def indicator_effect(indicator: InternationalIndicator) -> str:
    move = move_score(indicator.change_1d, indicator.change_5d)
    if indicator.key == "sox":
        if move >= 1:
            return "半導體風險偏好改善，對台積電、IC 設計、AI 伺服器供應鏈偏多"
        if move <= -1:
            return "半導體回檔，台股電子權值股開盤與評價可能承壓"
        return "半導體動能中性，個股仍看訂單與營收能見度"
    if indicator.key == "nasdaq":
        if move >= 1:
            return "美國科技股走強，有利台股 AI 與電子族群風險胃納"
        if move <= -1:
            return "科技股風險偏好降溫，高本益比成長股須防估值修正"
        return "科技股訊號中性，台股仍需看半導體與匯率"
    if indicator.key == "sp500":
        if move >= 0.8:
            return "全球風險資產偏多，外資回補台股機率提高"
        if move <= -0.8:
            return "全球風險資產轉弱，台股需防賣壓外溢"
        return "全球風險胃納中性"
    if indicator.key == "us10y":
        delta = indicator.delta_1d or 0.0
        if delta >= 0.05:
            return "殖利率上升，成長股估值與高股息 ETF 可能承壓"
        if delta <= -0.05:
            return "殖利率下降，有利科技成長股與高股息評價"
        return "利率變動有限，估值壓力中性"
    if indicator.key == "dxy":
        if move >= 0.5:
            return "美元轉強，可能壓抑新興市場資金流；出口股有匯兌支撐"
        if move <= -0.5:
            return "美元轉弱，有利外資流向新興市場與台股評價"
        return "美元變動有限，外資匯率壓力中性"
    if indicator.key == "usdtwd":
        if move >= 0.4:
            return "台幣偏貶，出口商有匯兌助力，但需留意外資流出"
        if move <= -0.4:
            return "台幣偏升，外資流向較友善，但出口匯兌利益可能收斂"
        return "台幣區間震盪，匯率影響中性"
    if indicator.key == "vix":
        if indicator.latest >= 25 or move >= 5:
            return "波動率升溫，短線震盪風險提高，分批間距宜拉大"
        if move <= -5:
            return "波動率降溫，市場風險情緒改善"
        return "波動率中性，仍需觀察美股風險情緒"
    if indicator.key == "wti":
        if move >= 3:
            return "油價上升，運輸、塑化與通膨壓力增加；能源相關受惠"
        if move <= -3:
            return "油價下跌，成本與通膨壓力下降，對多數產業偏正面"
        return "油價變動有限，成本壓力中性"
    if indicator.key == "gold":
        if move >= 1:
            return "黃金走高，代表避險需求升溫，股市風險偏好需保守看待"
        if move <= -1:
            return "黃金回落，避險需求降溫，風險資產壓力減輕"
        return "避險需求變動不大"
    return "需搭配其他指標判讀"


def international_summary_lines(
    indicators: list[InternationalIndicator],
) -> list[str]:
    if not indicators:
        return [
            "國際市場資料暫時無法取得；今日仍應自行檢查美股、匯率、利率與能源價格。",
        ]
    data = indicator_map(indicators)
    bias, score = international_bias(indicators)
    lines = [f"綜合判斷：國際盤勢對台股為「{bias}」（分數 {score:.1f}）。"]
    sox = data.get("sox")
    nasdaq = data.get("nasdaq")
    if sox and nasdaq:
        lines.append(
            "科技與半導體："
            f"Nasdaq 1日 {fmt_change(nasdaq.change_1d)}、SOX 1日 {fmt_change(sox.change_1d)}，"
            f"{indicator_effect(sox)}。"
        )
    us10y = data.get("us10y")
    dxy = data.get("dxy")
    usdtwd = data.get("usdtwd")
    if us10y or dxy or usdtwd:
        parts = []
        if us10y:
            parts.append(f"美債10年 {us10y.latest:.2f}%（1日 {fmt_delta_bps(us10y.delta_1d)}）")
        if dxy:
            parts.append(f"美元指數 1日 {fmt_change(dxy.change_1d)}")
        if usdtwd:
            parts.append(f"美元／台幣 {usdtwd.latest:.3f}")
        lines.append("利率與匯率：" + "、".join(parts) + "；" + (indicator_effect(us10y) if us10y else "需留意資金流向") + "。")
    vix = data.get("vix")
    if vix:
        lines.append(
            f"波動風險：VIX {vix.latest:.2f}（1日 {fmt_change(vix.change_1d)}）；"
            f"{indicator_effect(vix)}。"
        )
    wti = data.get("wti")
    gold = data.get("gold")
    if wti or gold:
        parts = []
        if wti:
            parts.append(f"WTI 1日 {fmt_change(wti.change_1d)}")
        if gold:
            parts.append(f"黃金 1日 {fmt_change(gold.change_1d)}")
        lines.append("商品與避險：" + "、".join(parts) + "；" + (indicator_effect(wti) if wti else indicator_effect(gold)) + "。")
    return lines


def international_row(indicator: InternationalIndicator) -> str:
    latest = f"{indicator.latest:.2f}{indicator.unit}"
    if indicator.key == "usdtwd":
        latest = f"{indicator.latest:.3f}{indicator.unit}"
    change_1d = fmt_delta_bps(indicator.delta_1d) if indicator.key == "us10y" else fmt_change(indicator.change_1d)
    change_5d = fmt_delta_bps(indicator.delta_5d) if indicator.key == "us10y" else fmt_change(indicator.change_5d)
    return (
        f"| {indicator.name} | {latest} | {indicator.latest_date} | "
        f"{change_1d} | {change_5d} | {indicator_effect(indicator)} |"
    )


def international_section(indicators: list[InternationalIndicator]) -> list[str]:
    lines = ["## 國際情勢與台股影響", ""]
    for summary in international_summary_lines(indicators):
        lines.append(f"- {summary}")
    lines.extend(
        [
            "",
            "| 指標 | 最新 | 日期 | 1日變化 | 5日變化 | 對台股可能影響 |",
            "|---|---:|---|---:|---:|---|",
        ]
    )
    if indicators:
        lines.extend(international_row(indicator) for indicator in indicators)
    else:
        lines.append("| 國際市場資料 | — | — | — | — | 資料暫時無法取得，請查看原始資料來源。 |")
    lines.append("")
    return lines


def pct_text(value: float | None) -> str:
    return fmt(value, "%") if value is not None else "—"


def market_health_section(health: MarketHealth) -> list[str]:
    breadth_total = health.up_count + health.down_count + health.flat_count
    above_ratio = (
        health.above_ma20_count / health.above_ma20_total * 100
        if health.above_ma20_total
        else None
    )
    lines = [
        "## 2.5 台股大盤與資金健康度",
        "",
        f"- 市場行情資料日：{health.data_date or '—'}。",
        f"- 類股成交比重：電子股 {pct_text(health.electronic_ratio)}、"
        f"金融股 {pct_text(health.financial_ratio)}、"
        f"傳產股 {pct_text(health.traditional_ratio)}。"
        "用來監控資金是否過度集中在單一族群。",
        f"- 散戶情緒（小台代理指標）：{health.retail_mtx_sentiment}。",
        f"- 市場廣度：今日上漲 {health.up_count:,} 家、下跌 {health.down_count:,} 家、"
        f"平盤 {health.flat_count:,} 家，共 {breadth_total:,} 家。",
        f"- 站上 20 日線個股比例：候選池 {pct_text(above_ratio)} "
        f"({health.above_ma20_count}/{health.above_ma20_total})；"
        "全市場 20 日線需大量逐檔歷史行情，目前先以報告候選池估算。",
        "",
    ]
    return lines


def chip_section(context: ChipContext) -> list[str]:
    foreign = context.institutional_nets.get("Foreign_Investor")
    trust = context.institutional_nets.get("Investment_Trust")
    dealer = (
        context.institutional_nets.get("Dealer_self", 0.0)
        + context.institutional_nets.get("Dealer_Hedging", 0.0)
    )
    option_net = context.foreign_option_net_amount
    option_direction = "偏多" if (option_net or 0) > 0 else "偏空" if (option_net or 0) < 0 else "中性"
    lines = [
        "## 2.6 籌碼與信用交易動態",
        "",
        f"- 三大法人：{context.institutional_date or '—'} 現貨合計買賣超 "
        f"{marked_money_yi(context.institutional_total_net)}；外資 {marked_money_yi(foreign)}、"
        f"投信 {marked_money_yi(trust)}、自營商 {marked_money_yi(dealer)}。",
        "- 投信連續買超前 3 名（報告候選池）："
        + ("；".join(context.investment_trust_streaks) if context.investment_trust_streaks else "暫無連續買超資料。"),
        "- 外資連續賣超前 3 名（報告候選池）："
        + ("；".join(context.foreign_sell_streaks) if context.foreign_sell_streaks else "暫無連續賣超資料。"),
        f"- 期權佈局：{context.futures_date or '—'} 外資台指期淨未平倉 "
        f"{marked_signed_int(context.foreign_tx_net_open_interest, ' 口')} "
        f"(增減 {marked_signed_int(context.foreign_tx_net_delta, ' 口')})；"
        f"{context.option_date or '—'} 外資選擇權多空金額 {fmt_thousand_amount_yi(option_net)}（{option_direction}）。",
        f"- 信用交易：{context.margin_date or '—'} 融資餘額變動 "
        f"{fmt_money_yi(context.margin_money_delta)}，融資維持率 "
        f"{pct_text(context.maintenance_ratio)}；融券餘額變動 "
        f"{fmt_signed_int(context.short_sale_delta, ' 張')}。",
    ]
    if context.maintenance_ratio is None:
        lines.append(f"- 融資維持率註記：{context.maintenance_note}。")
    lines.append("")
    return lines


def executive_summary_section(
    indicators: list[InternationalIndicator],
    health: MarketHealth,
    chips: ChipContext,
    forecast: NextSessionForecast | None = None,
) -> list[str]:
    breadth_total = health.up_count + health.down_count + health.flat_count
    if breadth_total:
        if health.up_count > health.down_count * 1.2:
            breadth = (
                f"🔴 今日漲多跌少，上漲 {health.up_count:,} 家、"
                f"下跌 {health.down_count:,} 家。"
            )
        elif health.down_count > health.up_count * 1.2:
            breadth = (
                f"🟢 今日跌多漲少，上漲 {health.up_count:,} 家、"
                f"下跌 {health.down_count:,} 家。"
            )
        else:
            breadth = (
                f"⚪ 今日漲跌家數接近，上漲 {health.up_count:,} 家、"
                f"下跌 {health.down_count:,} 家。"
            )
    else:
        breadth = "⚪ 市場廣度資料暫缺。"

    sector = (
        f"電子股 {pct_text(health.electronic_ratio)}、"
        f"金融股 {pct_text(health.financial_ratio)}、"
        f"傳產股 {pct_text(health.traditional_ratio)}"
    )
    if health.electronic_ratio is not None and health.electronic_ratio >= 70:
        sector_note = "資金高度集中電子族群，需留意單一族群回檔風險"
    elif health.traditional_ratio is not None and health.traditional_ratio >= 30:
        sector_note = "傳產資金占比提高，盤面有避險或輪動跡象"
    else:
        sector_note = "資金分布未見極端失衡"

    foreign = chips.institutional_nets.get("Foreign_Investor")
    futures_delta = chips.foreign_tx_net_delta
    if foreign is None and futures_delta is None:
        chip_line = "⚪ 籌碼資料暫缺，需等待三大法人與期貨資料更新。"
    else:
        chip_parts = []
        if foreign is not None:
            action = "買超" if foreign > 0 else "賣超" if foreign < 0 else "持平"
            chip_parts.append(f"{direction_marker(foreign)} 外資現貨{action} {fmt_money_yi(abs(foreign))}")
        if futures_delta is not None:
            if chips.foreign_tx_net_open_interest is not None and chips.foreign_tx_net_open_interest < 0:
                futures_note = "淨空單增加" if futures_delta < 0 else "空單回補"
            else:
                futures_note = "期貨多單增加" if futures_delta > 0 else "期貨部位下降"
            chip_parts.append(
                f"{direction_marker(futures_delta)} 外資台指期{futures_note} "
                f"{abs(futures_delta):,} 口"
            )
        chip_line = "；".join(chip_parts) + "。"

    data = indicator_map(indicators)
    vix = data.get("vix")
    if vix:
        if vix.latest >= 25 or (vix.change_1d or 0) >= 5:
            risk_line = (
                f"⚠️ VIX {vix.latest:.2f}、1日 {fmt_change(vix.change_1d)}，"
                "短線波動加劇，建議拉大分批布局間距。"
            )
        else:
            risk_line = (
                f"⚪ VIX {vix.latest:.2f}、1日 {fmt_change(vix.change_1d)}，"
                "波動風險未見明顯升溫。"
            )
    else:
        risk_line = "⚪ VIX 資料暫缺，風險提示改以美股、利率與匯率綜合判讀。"

    current_forecast = forecast or neutral_next_session_forecast()
    forecast_marker = {
        "上漲": "🔴",
        "平盤": "⚪",
        "下跌": "🟢",
    }[current_forecast.label]

    return [
        "## 今日盤後速覽 (TL;DR)",
        "",
        f"- 大盤結構：{breadth}{sector}；{sector_note}。",
        f"- 籌碼動向：{chip_line}",
        f"- 風險提示：{risk_line}",
        f"- 次一交易日：{forecast_marker} 猜測{current_forecast.label}，"
        f"上漲／平盤／下跌機率為 {current_forecast.up_probability:.1f}%／"
        f"{current_forecast.flat_probability:.1f}%／"
        f"{current_forecast.down_probability:.1f}%，可信度"
        f"{current_forecast.confidence}。",
        "",
    ]


def fmt_return(value: float | None, threshold: float) -> str:
    text = fmt(value, "%")
    return strong_if(text, value is not None and value >= threshold)


def combined_return_text(security: Security) -> str:
    m = security.metrics
    return f"{fmt_return(m.get('ret20'), 20.0)} / {fmt_return(m.get('ret60'), 40.0)}"


def keyword_news(security: Security) -> str:
    if security.news_titles:
        return security.news_titles[0]
    if security.events:
        return security.events[0]
    return security.reasons[0] if security.reasons else "—"


def risk_price_text(security: Security) -> str:
    volatility = security.metrics.get("volatility20") or 40.0
    stop_pct = 0.08 if volatility < 35 else 0.10 if volatility < 55 else 0.12
    entry_low = security.close * 0.97
    stop = security.close * (1 - stop_pct)
    return f"{entry_low:.2f}~{security.close:.2f} / {stop:.2f}"


def stock_row_growth(security: Security) -> str:
    return (
        f"| {security.symbol} | {security.name} | {security.score:.1f} | "
        f"{combined_return_text(security)} | {fmt(security.revenue_yoy, '%')} | "
        f"{fmt(security.pe, '', 1)} | {keyword_news(security)} | "
        f"{risk_price_text(security)} |"
    )


def stock_row_financial(security: Security) -> str:
    eps_yoy = fmt(security.eps_yoy, "%")
    if security.eps_period and eps_yoy != "—":
        eps_yoy = f"{eps_yoy} ({security.eps_period})"
    equity_change = "—"
    if security.equity_change is not None:
        equity_change = (
            f"{direction_marker(security.equity_change)} "
            f"{fmt_money_yi(security.equity_change)} / "
            f"{fmt(security.equity_change_pct, '%')}"
        )
        if security.equity_date:
            equity_change += f" ({security.equity_date})"
    return (
        f"| {security.symbol} | {security.name} | {security.score:.1f} | "
        f"{combined_return_text(security)} | {eps_yoy} | "
        f"{fmt(security.pb, '', 2)} | {equity_change} |"
    )


ETF_EXPOSURES = {
    "0050": "台灣大型權值股：半導體、電子代工與金融龍頭",
    "0052": "台灣電子與半導體權值股",
    "0056": "台灣高股息成分股，偏成熟產業與現金流",
    "006208": "台灣大型權值股，與台股市值龍頭連動高",
    "00646": "美國 S&P 500 大型股，分散美股景氣循環",
    "00662": "Nasdaq 100 科技與成長股",
    "00757": "FANG+ 大型科技與 AI 平台股",
    "00830": "費城半導體成分股，連動全球半導體循環",
    "00878": "台灣 ESG 高股息與穩定配息族群",
    "00881": "台灣科技龍頭與半導體供應鏈",
    "00919": "台灣高息低波動族群",
    "00929": "台灣科技高股息族群",
    "00935": "台灣科技與成長主題股",
}


def etf_exposure(security: Security) -> str:
    return ETF_EXPOSURES.get(security.symbol, "ETF 成分股主題請以投信最新公告為準")


def etf_discount_note(security: Security) -> str:
    return "—（未取得同日官方 iNAV，不以收盤價估算）"


def etf_row(security: Security) -> str:
    reason = security.reasons[0] if security.reasons else etf_exposure(security)
    return (
        f"| {security.symbol} | {security.name} | {security.score:.1f} | "
        f"{security.label} | {combined_return_text(security)} | "
        f"{etf_exposure(security)} | {etf_discount_note(security)} | {reason} |"
    )


def calculate_metrics(history: list[dict[str, Any]]) -> dict[str, float | None]:
    closes = [number(row.get("close")) for row in history]
    closes = [value for value in closes if value is not None]
    volumes = [number(row.get("Trading_Volume")) for row in history]
    volumes = [value for value in volumes if value is not None]
    if len(closes) < 21:
        return {}

    latest = closes[-1]
    ma20 = statistics.fmean(closes[-20:])
    ma60 = statistics.fmean(closes[-60:]) if len(closes) >= 60 else None
    ret20 = pct_change(latest, closes[-21])
    ret60 = pct_change(latest, closes[-61]) if len(closes) >= 61 else None
    high60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
    drawdown60 = pct_change(latest, high60)

    daily_returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(max(1, len(closes) - 20), len(closes))
        if closes[index - 1]
    ]
    volatility20 = (
        statistics.pstdev(daily_returns) * math.sqrt(252) * 100
        if len(daily_returns) >= 2
        else None
    )
    volume_ratio = None
    if len(volumes) >= 20:
        avg20 = statistics.fmean(volumes[-20:])
        volume_ratio = statistics.fmean(volumes[-5:]) / avg20 if avg20 else None

    return {
        "latest": latest,
        "ma20": ma20,
        "ma60": ma60,
        "ret20": ret20,
        "ret60": ret60,
        "drawdown60": drawdown60,
        "volatility20": volatility20,
        "volume_ratio": volume_ratio,
    }


def score_security(security: Security) -> None:
    security.reasons.clear()
    security.risks = list(dict.fromkeys(security.risks))
    metrics = security.metrics
    if not metrics:
        security.score = 0
        security.label = "資料不足"
        security.risks.append("歷史行情不足，無法形成可靠評分")
        security.reasons.append("目前缺少足夠資料，無法辨識可靠的上漲催化劑")
        return

    latest = metrics["latest"] or security.close
    ma20 = metrics["ma20"] or latest
    ma60 = metrics["ma60"]
    ret20 = metrics["ret20"] or 0.0
    ret60 = metrics["ret60"] or 0.0
    drawdown = metrics["drawdown60"] or 0.0
    volatility = metrics["volatility20"] or 50.0
    volume_ratio = metrics["volume_ratio"] or 1.0

    trend_score = 0.0
    trend_score += 10 if latest > ma20 else 2
    trend_score += 10 if ma60 and latest > ma60 else 2
    trend_score += clamp((ret20 + 10) / 30 * 10, 0, 10)
    trend_score += clamp((ret60 + 15) / 50 * 10, 0, 10)
    trend_score += clamp((volume_ratio - 0.5) / 1.5 * 5, 0, 5)

    risk_score = clamp((45 - volatility) / 35 * 10, 0, 10)
    risk_score += clamp((drawdown + 25) / 25 * 5, 0, 5)
    liquidity_score = clamp(math.log10(max(security.value, 1)) - 6, 0, 3) / 3 * 5

    if security.is_etf:
        security.score = round(
            clamp((trend_score / 45 * 70) + risk_score + liquidity_score, 0, 100),
            1,
        )
    else:
        revenue_score = 0.0
        if security.revenue_yoy is not None:
            revenue_score += clamp((security.revenue_yoy + 10) / 40 * 18, 0, 18)
        if security.revenue_ytd_yoy is not None:
            revenue_score += clamp(
                (security.revenue_ytd_yoy + 10) / 40 * 12, 0, 12
            )

        value_score = 0.0
        if security.pe and security.pe > 0:
            value_score += clamp((55 - security.pe) / 45 * 6, 0, 6)
        if security.pb and security.pb > 0:
            value_score += clamp((8 - security.pb) / 7 * 4, 0, 4)

        security.score = round(
            clamp(
                trend_score
                + revenue_score
                + value_score
                + risk_score * 0.67
                + liquidity_score * 0.67,
                0,
                100,
            ),
            1,
        )

    if security.score >= 72:
        security.label = "建議投資"
    elif security.score >= 62:
        security.label = "建議觀察"
    elif security.score >= 50:
        security.label = "中性觀望"
    else:
        security.label = "暫不建議"

    confirmed_catalyst = False
    for event in security.events[:2]:
        security.reasons.append(f"公司事件：{event}")
        confirmed_catalyst = True

    if not security.is_etf and security.revenue_yoy is not None:
        if security.revenue_yoy >= 10:
            security.reasons.append(monthly_revenue_reason(security))
            confirmed_catalyst = True
        elif security.revenue_yoy >= 5:
            security.reasons.append(
                f"營運動能：最新月營收年增 {security.revenue_yoy:.1f}%，"
                "仍需確認後續訂單能否延續"
            )
        elif security.revenue_yoy < 0:
            security.risks.append(f"最新月營收年減 {abs(security.revenue_yoy):.1f}%")
        if security.revenue_yoy >= 100:
            security.risks.append("營收年增超過 100%，可能受低基期或認列時點影響")
    if (
        not security.is_etf
        and security.revenue_ytd_yoy is not None
        and security.revenue_ytd_yoy >= 10
    ):
        security.reasons.append(cumulative_revenue_reason(security))
        confirmed_catalyst = True

    security.reasons.append(f"潛在催化劑：{industry_catalyst(security)}")

    if not security.is_etf and not confirmed_catalyst:
        if security.label == "建議投資":
            security.label = "建議觀察"
        security.reasons.append(
            "目前沒有可驗證的公司利多事件或明顯營收成長，不應只因價格趨勢追價"
        )
    if any(keyword in security.industry for keyword in ("金融", "建材營造")):
        security.risks.append("產業營收認列方式特殊，不能只用單月年增判斷")
    if volatility >= 45:
        security.risks.append(f"年化波動約 {volatility:.1f}%，價格波動偏高")
    if drawdown <= -12:
        security.risks.append(f"距近 60 日高點 {abs(drawdown):.1f}%")
    if security.pe and security.pe >= 50:
        security.risks.append(f"本益比約 {security.pe:.1f} 倍，估值敏感")
    if not security.risks:
        security.risks.append("仍有市場、產業及個別公司事件風險")


def preliminary_score(security: Security) -> float:
    if security.is_etf:
        return math.log10(max(security.value, 1))
    if not security.symbol.isdigit() or len(security.symbol) != 4:
        return -999
    if security.close < 8 or security.value < 20_000_000:
        return -999
    revenue = clamp(security.revenue_yoy or -20, -30, 60)
    ytd = clamp(security.revenue_ytd_yoy or -20, -30, 60)
    liquidity = clamp(math.log10(max(security.value, 1)) - 7, 0, 4) * 5
    valuation = 0.0
    if security.pe and 0 < security.pe <= 60:
        valuation += (60 - security.pe) / 10
    return revenue * 0.45 + ytd * 0.35 + liquidity + valuation


def select_candidates(
    securities: dict[str, Security], config: dict[str, Any]
) -> list[Security]:
    stocks = [item for item in securities.values() if not item.is_etf]
    stocks.sort(key=preliminary_score, reverse=True)
    selected_symbols = {
        item.symbol for item in stocks[: int(config["max_stock_candidates"])]
    }
    selected_symbols.update(config.get("always_include_stocks", []))
    selected_symbols.update(config["etf_symbols"])
    return [
        securities[symbol]
        for symbol in selected_symbols
        if symbol in securities
    ]


def add_histories(
    candidates: list[Security],
    config: dict[str, Any],
    as_of: date | None = None,
) -> None:
    report_date = as_of or datetime.now(TAIPEI_TZ).date()
    start_date = report_date - timedelta(days=int(config["history_days"]))
    token = os.environ.get("FINMIND_TOKEN", "")
    for index, security in enumerate(candidates, start=1):
        print(f"[{index}/{len(candidates)}] 下載 {security.symbol} {security.name} 歷史行情")
        security.history = rows_on_or_before(
            finmind_history(security.symbol, start_date, token), report_date
        )
        security.metrics = calculate_metrics(security.history)
        score_security(security)


def fmt(value: float | None, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def detail_block(security: Security) -> str:
    reason_text = "；".join(security.reasons[:3])
    risk_text = "；".join(security.risks[:3])
    holding_line = ""
    if security.large_holder_pct is not None:
        holding_line = (
            f"- 集保千張以上持股比重：{security.large_holder_pct:.2f}%"
            f"（{security.large_holder_date or '日期暫缺'}）\n"
        )
    if security.is_etf:
        return (
            f"### {security.symbol} {security.name}｜{security.score:.1f} 分｜"
            f"{security.label}\n\n"
            f"- 建議原因：{reason_text}\n"
            f"- 主要風險：{risk_text}\n"
            f"- 最新收盤：{security.close:.2f}；近 20 日："
            f"{fmt(security.metrics.get('ret20'), '%')}；近 60 日："
            f"{fmt(security.metrics.get('ret60'), '%')}\n"
            f"- 主要曝險：{etf_exposure(security)}\n"
            f"- 實際折溢價幅度：{etf_discount_note(security)}\n"
        )
    if is_financial_stock(security):
        eps_yoy = fmt(security.eps_yoy, "%")
        if security.eps_period and eps_yoy != "—":
            eps_yoy += f"（{security.eps_period}）"
        equity_change = "—"
        if security.equity_change is not None:
            equity_change = (
                f"{fmt_money_yi(security.equity_change)} / "
                f"{fmt(security.equity_change_pct, '%')}"
                f"（{security.equity_date or '日期暫缺'}）"
            )
        return (
            f"### {security.symbol} {security.name}｜{security.score:.1f} 分｜"
            f"{security.label}\n\n"
            f"- 建議原因：{reason_text}\n"
            f"- 主要風險：{risk_text}\n"
            f"- 最新收盤：{security.close:.2f}；近 20 日："
            f"{fmt(security.metrics.get('ret20'), '%')}；近 60 日："
            f"{fmt(security.metrics.get('ret60'), '%')}\n"
            f"- 累計 EPS 年增率：{eps_yoy}；股淨比 (PB)：{fmt(security.pb, '', 2)}\n"
            f"- 淨值季變動：{equity_change}\n"
            f"{holding_line}"
        )
    return (
        f"### {security.symbol} {security.name}｜{security.score:.1f} 分｜"
        f"{security.label}\n\n"
        f"- 建議原因：{reason_text}\n"
        f"- 主要風險：{risk_text}\n"
        f"- 最新收盤：{security.close:.2f}；近 20 日："
        f"{fmt(security.metrics.get('ret20'), '%')}；近 60 日："
        f"{fmt(security.metrics.get('ret60'), '%')}\n"
        f"- 月營收年增率 (YoY)：{fmt(security.revenue_yoy, '%')}；"
        f"累計營收年增率 (YoY)：{fmt(security.revenue_ytd_yoy, '%')}；"
        f"本益比 (PE)：{fmt(security.pe)}\n"
        f"{holding_line}"
    )


def report_title(mode: str, today: date) -> str:
    if mode == "weekly":
        iso = today.isocalendar()
        return f"台股第 {iso.week} 週研究彙整"
    if mode == "weekend":
        return "台股週末雙日研究報告"
    return "台股每日研究報告"


def build_markdown(
    mode: str,
    candidates: list[Security],
    generated_at: datetime,
    international: list[InternationalIndicator] | None = None,
    health: MarketHealth | None = None,
    chips: ChipContext | None = None,
    forecast: NextSessionForecast | None = None,
) -> str:
    stocks = sorted(
        [item for item in candidates if not item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    etfs = sorted(
        [item for item in candidates if item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    top_stocks = stocks[:8]
    top_etfs = etfs[:5]
    growth_stocks = [item for item in stocks if not is_financial_stock(item)][:8]
    financial_stocks = [item for item in stocks if is_financial_stock(item)][:8]
    title = report_title(mode, generated_at.date())
    current_forecast = forecast or neutral_next_session_forecast()

    intro = [
        f"# {title}",
        "",
        f"產生時間：{generated_at:%Y-%m-%d %H:%M}（Asia/Taipei）",
        "",
        "> 本報告依公開資料提出一般性量化投資建議，不考慮個人財務狀況、"
        "持倉與風險承受度；分數不代表未來報酬或保證獲利。",
        "",
    ]
    if mode == "weekend":
        intro.extend(
            [
                "週末休市，本報告使用最近交易日資料，重點放在近一週趨勢與下週觀察。",
                "",
            ]
        )
    elif mode == "weekly":
        intro.extend(
            [
                "本週彙整著重公司重大訊息、營收動能、產業催化劑與風險變化。",
                "",
            ]
        )
    intro.extend(
        [
            "建議原因優先採用公司重大訊息、營收與可辨識的產業事件；"
            "技術指標只參與排序，不作為股票上漲原因。",
            "",
        ]
    )

    lines = (
        intro
        + executive_summary_section(
            international or [],
            health or MarketHealth(),
            chips or ChipContext(),
            current_forecast,
        )
        + next_session_forecast_section(current_forecast)
        + international_section(international or [])
        + market_health_section(health or MarketHealth())
        + chip_section(chips or ChipContext())
        + [
        "## 建議投資股票及原因",
        "",
        "### 科技／傳產類",
        "",
        "| 代碼 | 名稱 | 分數 | 20D/60D報酬 | 月營收年增率 (YoY) | 本益比 (PE) | 關鍵字新聞 | 建議/停損價 |",
        "|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    lines.extend(stock_row_growth(item) for item in growth_stocks)
    lines.extend(
        [
            "",
            "### 金融類個股",
            "",
            "| 代碼 | 名稱 | 分數 | 20D/60D報酬 | 累計EPS年增率 (YoY) | 股淨比 (PB) | 淨值季變動 (QoQ) |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    lines.extend(stock_row_financial(item) for item in financial_stocks)
    lines.extend(
        [
            "",
            "## 建議投資 ETF 及原因",
            "",
            "| 代碼 | 名稱 | 分數 | 建議 | 20D/60D報酬 | 主要曝險/成分股主題 | 實際折溢價幅度 (%) | 建議原因 |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
    )
    lines.extend(etf_row(item) for item in top_etfs)
    lines.extend(["", "## 投資建議詳情", ""])
    for item in [*top_stocks[:5], *top_etfs[:3]]:
        lines.append(detail_block(item))

    lines.extend(
        [
            "## 使用方式",
            "",
            "- `建議投資`：分數與事件／營運催化劑相對完整，仍應核對財報、法說與重大訊息。",
            "- `建議觀察`：可能有產業題材，但公司事件、基本面或估值仍有缺口。",
            "- `暫不建議`：目前風險或弱勢訊號較多。",
            "- 建議分批布局並設定風險上限，不因單日排名改變而追價。",
            "",
            "## 資料來源與限制",
            "",
            "- TWSE OpenAPI：上市行情、估值、月營收與每日重大訊息。",
            "- TPEx OpenAPI：上櫃行情、估值、月營收與每日重大訊息。",
            "- TAIFEX OpenAPI：臺股期貨三大法人與小型臺指期貨未平倉部位。",
            "- TDCC OpenAPI：集保股權分散表與千張以上持股比重。",
            "- FinMind：個股與 ETF 歷史日行情、新聞、三大法人、期貨選擇權、融資融券與財報欄位。",
            "- Yahoo Finance Chart：美股指數、半導體、匯率、利率與商品價格。",
            "- 免費資料可能延遲、缺漏或更正；交易前應核對公開資訊觀測站與交易所。",
            "",
        ]
    )
    return "\n".join(lines)


def inline_html(value: str) -> str:
    escaped = html.escape(value)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def dashboard_metric(label: str, value: str, detail: str, tone: str = "neutral") -> str:
    return (
        f'<article class="kpi-card kpi-{tone}">'
        f'<p class="kpi-label">{html.escape(label)}</p>'
        f'<p class="kpi-value">{html.escape(value)}</p>'
        f'<p class="kpi-detail">{html.escape(detail)}</p>'
        "</article>"
    )


def dashboard_revenue_chart(stocks: list[Security]) -> str:
    rows = [item for item in stocks if item.revenue_yoy is not None][:6]
    if not rows:
        return '<p class="chart-empty">候選標的尚無可用月營收年增資料。</p>'
    maximum = max(abs(item.revenue_yoy or 0) for item in rows) or 1
    bars = []
    for item in rows:
        value = item.revenue_yoy or 0
        width = min(100, abs(value) / maximum * 100)
        direction = "up" if value >= 0 else "down"
        bars.append(
            '<div class="bar-row">'
            f'<span class="bar-label">{html.escape(item.symbol)} {html.escape(item.name)}</span>'
            '<span class="bar-track">'
            f'<span class="bar-fill {direction}" style="width:{width:.1f}%"></span>'
            "</span>"
            f'<strong class="bar-value {direction}">{value:+.1f}%</strong>'
            "</div>"
        )
    return "".join(bars)


def dashboard_breadth_chart(health: MarketHealth) -> str:
    total = health.up_count + health.down_count + health.flat_count
    if not total:
        return '<p class="chart-empty">市場廣度資料暫缺。</p>'
    values = [
        ("上漲", health.up_count, "up"),
        ("平盤", health.flat_count, "flat"),
        ("下跌", health.down_count, "down"),
    ]
    segments = "".join(
        f'<span class="breadth-{tone}" style="width:{count / total * 100:.2f}%"></span>'
        for _, count, tone in values
        if count
    )
    labels = "".join(
        f'<li><span class="legend-dot breadth-{tone}"></span>{label} {count:,} 家 '
        f'({count / total * 100:.1f}%)</li>'
        for label, count, tone in values
    )
    return f'<div class="breadth-track">{segments}</div><ul class="breadth-legend">{labels}</ul>'


def dashboard_forecast_chart(forecast: NextSessionForecast) -> str:
    rows = [
        ("上漲", forecast.up_probability, "up"),
        ("平盤", forecast.flat_probability, "flat"),
        ("下跌", forecast.down_probability, "down"),
    ]
    bars = []
    for label, value, tone in rows:
        bars.append(
            '<div class="forecast-row">'
            f'<span class="forecast-label">{label}</span>'
            '<span class="forecast-track">'
            f'<span class="forecast-fill {tone}" style="width:{value:.1f}%"></span>'
            "</span>"
            f'<strong>{value:.1f}%</strong>'
            "</div>"
        )
    return (
        "".join(bars)
        + f'<p class="chart-note">目前猜測：{html.escape(forecast.label)}｜'
        f'可信度：{html.escape(forecast.confidence)}｜'
        f'因子覆蓋率：{forecast.coverage * 100:.0f}%</p>'
    )


def dashboard_financial_rows(stocks: list[Security]) -> str:
    if not stocks:
        return '<tr><td colspan="7">候選標的資料暫缺。</td></tr>'
    rows = []
    for item in stocks[:8]:
        earnings = (
            fmt(item.eps_yoy, "%")
            if is_financial_stock(item)
            else fmt(item.revenue_ytd_yoy, "%")
        )
        valuation = (
            f"PB {fmt(item.pb, '', 2)}"
            if is_financial_stock(item)
            else f"PE {fmt(item.pe, '', 1)}"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.symbol)}</td>"
            f"<td>{html.escape(item.name)}</td>"
            f"<td>{item.score:.1f}</td>"
            f"<td>{fmt(item.revenue_yoy, '%')}</td>"
            f"<td>{earnings}</td>"
            f"<td>{html.escape(valuation)}</td>"
            f"<td>{html.escape(item.label)}</td>"
            "</tr>"
        )
    return "".join(rows)


def finance_report_dashboard(
    title: str,
    mode: str,
    candidates: list[Security],
    generated_at: datetime,
    health: MarketHealth,
    chips: ChipContext,
    forecast: NextSessionForecast | None = None,
) -> str:
    """Render the finance-report skill as a self-contained report overview."""
    current_forecast = forecast or neutral_next_session_forecast()
    stocks = sorted(
        [item for item in candidates if not item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    etfs = sorted(
        [item for item in candidates if item.is_etf and item.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    positive_revenue = sum(
        1 for item in stocks if item.revenue_yoy is not None and item.revenue_yoy > 0
    )
    total_breadth = health.up_count + health.down_count + health.flat_count
    breadth = (
        f"{health.up_count:,} / {health.down_count:,}"
        if total_breadth
        else "資料暫缺"
    )
    institutional = chips.institutional_total_net
    institutional_value = marked_money_yi(institutional)
    institutional_detail = (
        f"{chips.institutional_date or '資料日期暫缺'} 三大法人現貨合計"
        if institutional is not None
        else "三大法人資料暫缺"
    )
    sector_detail = (
        f"電子 {pct_text(health.electronic_ratio)}｜金融 {pct_text(health.financial_ratio)}"
    )
    top_reason = stocks[0].reasons[0] if stocks and stocks[0].reasons else "等待公司事件與營收資料確認"
    highlights = [
        f"次一交易日推估：{current_forecast.label}；上漲、平盤、下跌機率為 "
        f"{current_forecast.up_probability:.1f}%、{current_forecast.flat_probability:.1f}%、"
        f"{current_forecast.down_probability:.1f}%，可信度{current_forecast.confidence}。",
        f"市場廣度：上漲 {health.up_count:,} 家、下跌 {health.down_count:,} 家，報告以全市場漲跌家數判讀盤面結構。",
        f"營收動能：{positive_revenue}/{len(stocks)} 檔非金融候選標的月營收年增；月營收只作基本面線索，不單獨解釋股價。",
        f"資金健康度：{sector_detail}；類股成交比重用於辨識資金是否過度集中。",
        f"籌碼觀察：{institutional_detail}為 {institutional_value}，法人流向需和公司營運與重大訊息交叉確認。",
        f"最高分候選：{stocks[0].symbol} {stocks[0].name}，首要研究線索為「{top_reason}」。" if stocks else "候選清單資料暫缺。",
    ]
    outlook = (
        f"次一交易日目前以{current_forecast.label}情境機率最高，但仍須驗證"
        f"{current_forecast.invalidation}"
        "公司研究部分則持續檢查月營收、獲利與重大訊息是否支持需求改善。"
    )
    mode_label = "週度彙整" if mode == "weekly" else "每日盤後"
    return f"""
<section class="finance-dashboard" aria-label="財務總覽">
  <header class="finance-masthead">
    <p class="eyebrow">TAIWAN STOCK RESEARCH · {html.escape(mode_label)}</p>
    <h1>{html.escape(title)}</h1>
    <p>資料時間：{generated_at:%Y-%m-%d %H:%M}（Asia/Taipei）｜以公開資料建立的研究總覽</p>
  </header>
  <section class="kpi-grid" aria-label="核心 KPI">
    {dashboard_metric("次一交易日推估", current_forecast.label, f"漲 {current_forecast.up_probability:.1f}%｜平 {current_forecast.flat_probability:.1f}%｜跌 {current_forecast.down_probability:.1f}%", "up" if current_forecast.label == "上漲" else "down" if current_forecast.label == "下跌" else "neutral")}
    {dashboard_metric("市場廣度", breadth, "上漲 / 下跌家數", "up" if health.up_count >= health.down_count else "down")}
    {dashboard_metric("電子成交比重", pct_text(health.electronic_ratio), sector_detail, "neutral")}
    {dashboard_metric("法人現貨合計", institutional_value, institutional_detail, "up" if (institutional or 0) > 0 else "down" if (institutional or 0) < 0 else "neutral")}
    {dashboard_metric("研究候選", f"{len(stocks)} 檔 / {len(etfs)} 檔", "股票 / ETF", "neutral")}
  </section>
  <section class="dashboard-grid">
    <article class="dashboard-panel">
      <div class="panel-heading"><p>Revenue Momentum</p><h2>候選標的月營收年增率</h2></div>
      {dashboard_revenue_chart(stocks)}
    </article>
    <article class="dashboard-panel">
      <div class="panel-heading"><p>Market Breadth</p><h2>市場廣度與壓力</h2></div>
      {dashboard_breadth_chart(health)}
      <p class="chart-note">以漲跌家數替代企業燒錢指標，避免把不適用的公司財報概念套到台股大盤。</p>
    </article>
    <article class="dashboard-panel">
      <div class="panel-heading"><p>Next Session Scenario</p><h2>次一交易日方向機率</h2></div>
      {dashboard_forecast_chart(current_forecast)}
    </article>
  </section>
  <section class="dashboard-panel financial-summary">
    <div class="panel-heading"><p>Financial Snapshot</p><h2>候選標的財務與估值摘要</h2></div>
    <div class="table-scroll"><table><thead><tr><th>代碼</th><th>名稱</th><th>分數</th><th>月營收 YoY</th><th>累計營收／EPS YoY</th><th>估值</th><th>結論</th></tr></thead>
    <tbody>{dashboard_financial_rows(stocks)}</tbody></table></div>
  </section>
  <section class="dashboard-grid">
    <article class="dashboard-panel"><div class="panel-heading"><p>Highlights</p><h2>本期重點</h2></div><ol class="highlight-list">{''.join(f'<li>{html.escape(item)}</li>' for item in highlights)}</ol></article>
    <article class="dashboard-panel"><div class="panel-heading"><p>Outlook</p><h2>下期觀察</h2></div><p class="outlook-copy">{html.escape(outlook)}</p></article>
  </section>
  <details class="methodology"><summary>方法論與資料限制</summary><p>候選排序綜合營收、估值、流動性、波動與價格資料；投資原因優先採公司重大訊息、營收與產業事件。次一交易日機率使用國際盤、台股市場廣度、外資現貨、外資台指期與匯率建立規則型情境分數；平盤定義為收盤漲跌介於 ±0.3%，單一方向機率上限為 60%，模型不預測點位，也不保證報酬。均線、短期報酬與成交量只參與排序，不作為股價上漲原因。資料源包括 TWSE、TPEx、TAIFEX、TDCC、FinMind 與 Yahoo Finance，可能延遲、缺漏或修正，交易前請核對公司公告與正式財報。</p></details>
</section>
"""


def markdown_to_html(markdown: str, title: str, dashboard: str = "") -> str:
    blocks: list[str] = []
    in_table = False
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [inline_html(cell.strip()) for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            if not in_table:
                blocks.append("<table>")
                in_table = True
            tag = "th" if not any("<tr>" in item for item in blocks[-2:]) else "td"
            blocks.append(
                "<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>"
            )
            continue
        if in_table:
            blocks.append("</table>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{inline_html(line[2:])}</li>")
            continue
        if in_list:
            blocks.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("### "):
            blocks.append(f"<h3>{inline_html(line[4:])}</h3>")
        elif line.startswith("## "):
            blocks.append(f"<h2>{inline_html(line[3:])}</h2>")
        elif line.startswith("# "):
            if dashboard and line[2:] == title:
                continue
            blocks.append(f"<h1>{inline_html(line[2:])}</h1>")
        elif line.startswith("> "):
            blocks.append(f"<blockquote>{inline_html(line[2:])}</blockquote>")
        else:
            blocks.append(f"<p>{inline_html(line)}</p>")
    if in_table:
        blocks.append("</table>")
    if in_list:
        blocks.append("</ul>")

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#617087; --line:#dce3ed; --navy:#102a56; --panel:#ffffff; --canvas:#f4f7fb; --red:#cf3945; --green:#16835e; --gold:#d9a441; }}
* {{ box-sizing: border-box; }}
body {{ font-family: system-ui, "Microsoft JhengHei", sans-serif; max-width: 1180px;
       margin: 32px auto; padding: 0 20px 56px; color: var(--ink); line-height: 1.65; background:var(--canvas); }}
body > h1, body > h2, body > h3, body > p, body > ul, body > blockquote, body > table {{ background:var(--panel); }}
h1, h2, h3 {{ color: var(--navy); }} h2 {{ margin-top: 40px; }}
blockquote {{ border-left: 4px solid var(--gold); margin: 16px 0; padding: 10px 16px; background: #fff8e8; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0 28px; font-size: 14px; background:var(--panel); }}
th, td {{ border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align:top; }}
th {{ background: #edf3fb; position: sticky; top: 0; z-index: 1; }} tr:nth-child(even) {{ background: #f8fafc; }}
.finance-dashboard {{ margin:0 0 38px; }}
.finance-masthead {{ padding:32px; border-radius:20px; color:#fff; background:linear-gradient(135deg,#102a56,#215a93 62%,#2f8b8b); box-shadow:0 12px 30px #102a5630; }}
.finance-masthead h1 {{ margin:5px 0 8px; color:#fff; font-size:clamp(28px,4vw,44px); line-height:1.2; }} .finance-masthead p {{ margin:0; color:#e8f0fb; }}
.eyebrow,.panel-heading p,.kpi-label {{ margin:0; color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.09em; text-transform:uppercase; }} .finance-masthead .eyebrow {{ color:#b9d9ff; }}
.kpi-grid,.dashboard-grid {{ display:grid; gap:16px; margin-top:16px; }} .kpi-grid {{ grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }} .dashboard-grid {{ grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); }}
.kpi-card,.dashboard-panel {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:20px; box-shadow:0 4px 14px #102a5609; }}
.kpi-card {{ border-top:4px solid #8ca0b8; }} .kpi-up {{ border-top-color:var(--red); }} .kpi-down {{ border-top-color:var(--green); }} .kpi-value {{ margin:7px 0 3px; color:var(--navy); font-size:25px; font-weight:800; }} .kpi-detail,.chart-note {{ margin:0; color:var(--muted); font-size:13px; }}
.panel-heading h2 {{ margin:2px 0 18px; font-size:20px; }} .bar-row {{ display:grid; grid-template-columns:minmax(95px,1fr) 2fr 62px; gap:9px; align-items:center; margin:12px 0; font-size:13px; }} .bar-label {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }} .bar-track {{ height:10px; overflow:hidden; border-radius:99px; background:#e7edf5; }} .bar-fill {{ display:block; height:100%; border-radius:inherit; }} .bar-fill.up {{ background:var(--red); }} .bar-fill.down {{ background:var(--green); }} .bar-value {{ text-align:right; }} .bar-value.up {{ color:var(--red); }} .bar-value.down {{ color:var(--green); }}
.breadth-track {{ display:flex; height:24px; overflow:hidden; border-radius:99px; background:#e7edf5; }} .breadth-up {{ background:var(--red); }} .breadth-flat {{ background:#9daabd; }} .breadth-down {{ background:var(--green); }} .breadth-legend {{ display:flex; flex-wrap:wrap; gap:12px; padding:0; margin:16px 0 0; list-style:none; font-size:13px; }} .legend-dot {{ display:inline-block; width:9px; height:9px; margin-right:5px; border-radius:50%; }}
.forecast-row {{ display:grid; grid-template-columns:42px 1fr 54px; gap:10px; align-items:center; margin:13px 0; font-size:13px; }} .forecast-track {{ height:13px; overflow:hidden; border-radius:99px; background:#e7edf5; }} .forecast-fill {{ display:block; height:100%; border-radius:inherit; }} .forecast-fill.up {{ background:var(--red); }} .forecast-fill.flat {{ background:#9daabd; }} .forecast-fill.down {{ background:var(--green); }} .forecast-row strong {{ text-align:right; }}
.financial-summary {{ margin-top:16px; }} .table-scroll {{ overflow-x:auto; }} .financial-summary table {{ margin:0; }} .highlight-list {{ margin:0; padding-left:20px; }} .highlight-list li {{ margin:10px 0; }} .outlook-copy {{ margin:0; font-size:16px; }} .methodology {{ margin-top:16px; padding:16px 20px; border:1px solid var(--line); border-radius:14px; background:#edf3fb; }} .methodology summary {{ color:var(--navy); cursor:pointer; font-weight:800; }} .methodology p {{ margin:12px 0 0; color:#43516a; }} .chart-empty {{ margin:0; color:var(--muted); }}
@media (max-width:800px) {{ body {{ margin:0 auto; padding:12px 12px 38px; }} .kpi-grid,.dashboard-grid {{ grid-template-columns:1fr 1fr; }} .finance-masthead {{ padding:24px; }} }}
@media (max-width:520px) {{ .kpi-grid,.dashboard-grid {{ grid-template-columns:1fr; }} .bar-row {{ grid-template-columns:90px 1fr 54px; gap:6px; }} table {{ font-size:12px; }} th,td {{ padding:6px; }} }}
</style>
</head>
<body>
{dashboard}
{''.join(blocks)}
</body>
</html>
"""


def output_paths(mode: str, today: date) -> tuple[Path, Path]:
    if mode == "weekly":
        folder = REPORTS_DIR / "weekly"
        stem = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    elif mode == "weekend":
        folder = REPORTS_DIR / "weekend"
        stem = today.isoformat()
    else:
        folder = REPORTS_DIR / "daily"
        stem = today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stem}.md", folder / f"{stem}.html"


def run(mode: str) -> tuple[Path, Path]:
    generated_at = datetime.now(TAIPEI_TZ)
    report_date = generated_at.date()
    config = load_config()
    securities = load_market_data(config)
    candidates = select_candidates(securities, config)
    add_histories(candidates, config, report_date)
    attach_news_and_financials(candidates, report_date)
    attach_tdcc_holdings(candidates, report_date)
    international = load_international_context(report_date)
    health = market_health(securities, candidates)
    health.retail_mtx_sentiment = load_non_institution_mtx_sentiment(report_date)
    chips = load_chip_context(candidates, report_date)
    forecast = estimate_next_session(international, health, chips, report_date)
    markdown = build_markdown(
        mode,
        candidates,
        generated_at,
        international,
        health,
        chips,
        forecast,
    )
    title = report_title(mode, report_date)
    md_path, html_path = output_paths(mode, report_date)
    md_path.write_text(markdown, encoding="utf-8")
    dashboard = finance_report_dashboard(
        title, mode, candidates, generated_at, health, chips, forecast
    )
    html_path.write_text(markdown_to_html(markdown, title, dashboard), encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(markdown, encoding="utf-8")
    (REPORTS_DIR / "latest.html").write_text(
        markdown_to_html(markdown, title, dashboard), encoding="utf-8"
    )
    return md_path, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("daily", "weekly"),
        default="daily",
        help="報告類型",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    markdown_path, html_path = run(args.mode)
    print(f"Markdown: {markdown_path}")
    print(f"HTML: {html_path}")
