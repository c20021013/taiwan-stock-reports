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
    return MarketHealth(
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

    return [
        "## 今日盤後速覽 (TL;DR)",
        "",
        f"- 大盤結構：{breadth}{sector}；{sector_note}。",
        f"- 籌碼動向：{chip_line}",
        f"- 風險提示：{risk_line}",
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
            international or [], health or MarketHealth(), chips or ChipContext()
        )
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


def markdown_to_html(markdown: str, title: str) -> str:
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
body {{ font-family: system-ui, "Microsoft JhengHei", sans-serif; max-width: 1180px;
       margin: 32px auto; padding: 0 20px; color: #172033; line-height: 1.65; }}
h1, h2, h3 {{ color: #102a56; }}
blockquote {{ border-left: 4px solid #d9a441; margin: 16px 0; padding: 10px 16px;
             background: #fff8e8; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0 28px; font-size: 14px; }}
th, td {{ border: 1px solid #dce3ed; padding: 8px; text-align: left; }}
th {{ background: #edf3fb; }}
tr:nth-child(even) {{ background: #f8fafc; }}
</style>
</head>
<body>
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
    markdown = build_markdown(mode, candidates, generated_at, international, health, chips)
    title = report_title(mode, report_date)
    md_path, html_path = output_paths(mode, report_date)
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, title), encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(markdown, encoding="utf-8")
    (REPORTS_DIR / "latest.html").write_text(
        markdown_to_html(markdown, title), encoding="utf-8"
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
