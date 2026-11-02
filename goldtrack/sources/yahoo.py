"""Yahoo Finance daily chart source (free fallback, no API key)."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from ..models import Bar
from .base import SourceError
from .http import get_json

YAHOO_CHART_URL = (
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?range={range}&interval=1d"
)


def parse_yahoo_payload(payload) -> list[Bar]:
    """Parse a Yahoo Finance ``v8/finance/chart`` response."""
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote_rows = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise SourceError("Yahoo 接口未返回行情数据") from exc

    bars: list[Bar] = []
    for index, timestamp in enumerate(timestamps):
        try:
            open_v = quote_rows["open"][index]
            high_v = quote_rows["high"][index]
            low_v = quote_rows["low"][index]
            close_v = quote_rows["close"][index]
            volume_v = quote_rows["volume"][index]
        except (KeyError, IndexError, TypeError):
            continue
        if close_v is None or close_v <= 0:
            continue
        bar_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        open_v = close_v if open_v is None or open_v <= 0 else open_v
        high_v = max(open_v, close_v) if high_v is None else high_v
        low_v = min(open_v, close_v) if low_v is None else low_v
        bars.append(
            Bar(
                date=bar_date,
                open=float(open_v),
                high=float(high_v),
                low=float(low_v),
                close=float(close_v),
                volume=float(volume_v or 0.0),
            )
        )
    if not bars:
        raise SourceError("Yahoo 接口没有可用行情行")
    return sorted(bars, key=lambda item: item.date)


class YahooSource:
    """Daily bars from Yahoo Finance, e.g. COMEX gold futures ``GC=F``."""

    name = "yahoo"

    def fetch(self, symbol: str, range_: str = "max") -> list[Bar]:
        url = YAHOO_CHART_URL.format(symbol=quote(symbol, safe="="), range=range_)
        payload = get_json(url)
        return parse_yahoo_payload(payload)
