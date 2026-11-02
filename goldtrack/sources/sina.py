"""Sina Finance daily K-line sources (free, no API key)."""

from __future__ import annotations

import json
import re
from datetime import date

from ..models import Bar
from .base import SourceError
from .http import get_text

SINA_REFERER = "https://finance.sina.com.cn/"

_GLOBAL_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{symbol}=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}"
)
_DOMESTIC_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
)

_JSONP_BODY = re.compile(r"\((\[.*\])\)", re.S)


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sina_jsonp(text: str) -> list[dict]:
    """Extract the JSON array from a Sina JSONP response body."""
    match = _JSONP_BODY.search(text)
    if not match:
        raise SourceError("新浪接口未返回有效数据（可能是空值或限流）")
    try:
        rows = json.loads(match.group(1))
    except ValueError as exc:
        raise SourceError(f"新浪数据解析失败: {exc}") from exc
    if not isinstance(rows, list):
        raise SourceError("新浪接口返回结构异常")
    return rows


def _make_bar(
    day,
    open_,
    high,
    low,
    close,
    volume,
) -> Bar | None:
    open_v = _to_float(open_)
    high_v = _to_float(high)
    low_v = _to_float(low)
    close_v = _to_float(close)
    if close_v is None or close_v <= 0:
        return None
    try:
        parsed = date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        return None
    # Some free feeds omit open/high/low; fall back to close so the bar is usable.
    open_v = close_v if open_v is None or open_v <= 0 else open_v
    high_v = max(open_v, close_v) if high_v is None else high_v
    low_v = min(open_v, close_v) if low_v is None else low_v
    volume_v = _to_float(volume) or 0.0
    return Bar(
        date=parsed,
        open=open_v,
        high=high_v,
        low=low_v,
        close=close_v,
        volume=volume_v,
    )


def parse_global_rows(rows: list[dict]) -> list[Bar]:
    """Parse ``GlobalFuturesService`` rows (date/open/high/low/close)."""
    bars: list[Bar] = []
    for row in rows:
        bar = _make_bar(
            row.get("date"),
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("volume"),
        )
        if bar is not None:
            bars.append(bar)
    return sorted(bars, key=lambda item: item.date)


def parse_domestic_rows(rows: list[dict]) -> list[Bar]:
    """Parse ``InnerFuturesNewService`` rows (d/o/h/l/c/v)."""
    bars: list[Bar] = []
    for row in rows:
        bar = _make_bar(
            row.get("d"),
            row.get("o"),
            row.get("h"),
            row.get("l"),
            row.get("c"),
            row.get("v"),
        )
        if bar is not None:
            bars.append(bar)
    return sorted(bars, key=lambda item: item.date)


class SinaGlobalSource:
    """International daily bars such as XAU/USD spot or COMEX gold futures."""

    name = "sina_global"

    def fetch(self, symbol: str) -> list[Bar]:
        url = _GLOBAL_URL.format(symbol=symbol)
        text = get_text(url, headers={"Referer": SINA_REFERER})
        return parse_global_rows(parse_sina_jsonp(text))


class SinaDomesticSource:
    """Domestic SHFE futures daily bars, e.g. the gold main-continuous ``AU0``."""

    name = "sina_domestic"

    def fetch(self, symbol: str) -> list[Bar]:
        url = _DOMESTIC_URL.format(symbol=symbol)
        text = get_text(url, headers={"Referer": SINA_REFERER})
        return parse_domestic_rows(parse_sina_jsonp(text))
