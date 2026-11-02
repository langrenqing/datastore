"""Trend indicators: moving averages, daily change and annualised volatility."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from .config import ANNUAL_TRADING_DAYS
from .models import Bar

MA_WINDOWS: tuple[int, ...] = (5, 20, 60)
VOL_WINDOW = 20

TREND_BULL = "多头"
TREND_BEAR = "空头"
TREND_RANGE = "震荡"
TREND_UNKNOWN = "数据不足"


def bars_to_frame(bars: Iterable[Bar]) -> pd.DataFrame:
    """Convert bars to a date-sorted DataFrame with numeric OHLCV columns."""
    rows = [
        {
            "date": bar.date,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        for bar in bars
    ]
    frame = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    )
    if frame.empty:
        return frame
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame.reset_index(drop=True)


def _classify(close, ma5, ma20, ma60) -> str:
    values = (close, ma5, ma20, ma60)
    if any(value is None or (isinstance(value, float) and math.isnan(value)) for value in values):
        return TREND_UNKNOWN
    if close > ma20 > ma60 and ma5 >= ma20:
        return TREND_BULL
    if close < ma20 < ma60 and ma5 <= ma20:
        return TREND_BEAR
    return TREND_RANGE


def compute_indicators(
    frame: pd.DataFrame,
    ma_windows: Sequence[int] = MA_WINDOWS,
    vol_window: int = VOL_WINDOW,
    annualization: int = ANNUAL_TRADING_DAYS,
) -> pd.DataFrame:
    """Return a copy of ``frame`` with indicator columns added.

    Added columns: ``ma5``/``ma20``/``ma60`` (configurable), ``daily_change_pct``
    (percent), ``volatility_pct`` (annualised percent over ``vol_window``) and
    ``trend`` (多头/空头/震荡/数据不足).
    """
    if frame.empty:
        result = frame.copy()
        for window in ma_windows:
            result[f"ma{window}"] = pd.Series(dtype="float64")
        result["daily_change_pct"] = pd.Series(dtype="float64")
        result["volatility_pct"] = pd.Series(dtype="float64")
        result["trend"] = pd.Series(dtype="object")
        return result

    result = frame.sort_values("date").reset_index(drop=True).copy()
    for window in ma_windows:
        result[f"ma{window}"] = result["close"].rolling(window=window).mean()

    result["daily_change_pct"] = result["close"].pct_change() * 100.0
    log_returns = np.log(result["close"].where(result["close"] > 0)).diff()
    result["volatility_pct"] = (
        log_returns.rolling(window=vol_window).std(ddof=1)
        * math.sqrt(annualization)
        * 100.0
    )

    ma_columns = [f"ma{window}" for window in ma_windows]
    if "ma5" in result.columns and "ma20" in result.columns and "ma60" in result.columns:
        result["trend"] = [
            _classify(row["close"], row["ma5"], row["ma20"], row["ma60"])
            for _, row in result.iterrows()
        ]
    else:
        result["trend"] = TREND_RANGE
    return result


@dataclass(frozen=True)
class Snapshot:
    """Latest indicator values for one instrument."""

    date: object
    close: float
    daily_change_pct: float | None
    ma5: float | None
    ma20: float | None
    ma60: float | None
    volatility_pct: float | None
    trend: str


def _clean(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def latest_snapshot(frame: pd.DataFrame) -> Snapshot | None:
    """Return the most recent row's indicator snapshot, or ``None`` when empty."""
    if frame.empty:
        return None
    row = frame.iloc[-1]
    return Snapshot(
        date=row["date"],
        close=float(row["close"]),
        daily_change_pct=_clean(row.get("daily_change_pct")),
        ma5=_clean(row.get("ma5")),
        ma20=_clean(row.get("ma20")),
        ma60=_clean(row.get("ma60")),
        volatility_pct=_clean(row.get("volatility_pct")),
        trend=str(row.get("trend", TREND_UNKNOWN)),
    )
