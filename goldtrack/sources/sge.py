"""Shanghai Gold Exchange (SGE) daily quote source for Au99.99."""

from __future__ import annotations

from datetime import date

from ..models import Bar
from .base import SourceError
from .http import get_json

SGE_URL = "https://www.sge.com.cn/graph/Dailyhq?instid={symbol}"


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sge_payload(payload) -> list[Bar]:
    """Parse the SGE ``Dailyhq`` JSON body.

    Each row is ``[date, open, close, low, high]`` (SGE's documented order).
    """
    if not isinstance(payload, dict):
        raise SourceError("上金所接口返回结构异常")
    rows = payload.get("time")
    if not isinstance(rows, list) or not rows:
        raise SourceError("上金所接口未返回行情数据")

    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        raw_date, raw_open, raw_close, raw_low, raw_high = row[:5]
        try:
            parsed_date = date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            continue
        open_v = _to_float(raw_open)
        close_v = _to_float(raw_close)
        low_v = _to_float(raw_low)
        high_v = _to_float(raw_high)
        if close_v is None or close_v <= 0:
            continue
        open_v = close_v if open_v is None or open_v <= 0 else open_v
        high_v = max(open_v, close_v) if high_v is None else high_v
        low_v = min(open_v, close_v) if low_v is None else low_v
        bars.append(
            Bar(
                date=parsed_date,
                open=open_v,
                high=high_v,
                low=low_v,
                close=close_v,
                volume=0.0,
            )
        )
    if not bars:
        raise SourceError("上金所接口没有可用行情行")
    return sorted(bars, key=lambda item: item.date)


class SgeSource:
    """Daily Au99.99 (Shanghai gold) bars from the exchange website."""

    name = "sge"

    def fetch(self, symbol: str) -> list[Bar]:
        payload = get_json(SGE_URL.format(symbol=symbol))
        return parse_sge_payload(payload)
