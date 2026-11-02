import math
from datetime import date, timedelta

import numpy as np
import pytest

from goldtrack.indicators import (
    TREND_BEAR,
    TREND_BULL,
    TREND_RANGE,
    TREND_UNKNOWN,
    bars_to_frame,
    compute_indicators,
    latest_snapshot,
)
from goldtrack.models import Bar


def make_bars(closes, start=date(2026, 1, 1)):
    return [
        Bar(
            date=start + timedelta(days=index),
            open=float(close),
            high=float(close),
            low=float(close),
            close=float(close),
        )
        for index, close in enumerate(closes)
    ]


def test_moving_averages():
    frame = compute_indicators(bars_to_frame(make_bars(range(1, 71))))
    assert frame.loc[4, "ma5"] == pytest.approx(3.0)
    assert frame.loc[19, "ma20"] == pytest.approx(10.5)
    assert frame.loc[59, "ma60"] == pytest.approx(30.5)
    assert math.isnan(frame.loc[3, "ma5"])
    assert math.isnan(frame.loc[18, "ma20"])
    assert math.isnan(frame.loc[58, "ma60"])


def test_daily_change_pct():
    frame = compute_indicators(bars_to_frame(make_bars([100, 110, 99])))
    assert math.isnan(frame.loc[0, "daily_change_pct"])
    assert frame.loc[1, "daily_change_pct"] == pytest.approx(10.0)
    assert frame.loc[2, "daily_change_pct"] == pytest.approx(-10.0)


def test_annualised_volatility():
    closes = list(range(1, 71))
    frame = compute_indicators(bars_to_frame(make_bars(closes)))
    expected = (
        np.std(np.diff(np.log(closes))[:20], ddof=1) * math.sqrt(252) * 100
    )
    assert frame.loc[20, "volatility_pct"] == pytest.approx(expected)
    assert math.isnan(frame.loc[19, "volatility_pct"])


def test_constant_series_has_zero_volatility():
    frame = compute_indicators(bars_to_frame(make_bars([100] * 40)))
    assert frame.loc[39, "volatility_pct"] == pytest.approx(0.0)
    assert frame.loc[39, "daily_change_pct"] == pytest.approx(0.0)


def test_trend_classification():
    rising = compute_indicators(bars_to_frame(make_bars(range(1, 71))))
    assert rising.iloc[-1]["trend"] == TREND_BULL

    falling = compute_indicators(bars_to_frame(make_bars(range(70, 0, -1))))
    assert falling.iloc[-1]["trend"] == TREND_BEAR

    flat = compute_indicators(bars_to_frame(make_bars([100] * 70)))
    assert flat.iloc[-1]["trend"] == TREND_RANGE

    too_short = compute_indicators(bars_to_frame(make_bars(range(1, 10))))
    assert too_short.iloc[-1]["trend"] == TREND_UNKNOWN


def test_empty_frame():
    frame = compute_indicators(bars_to_frame([]))
    assert frame.empty
    assert "ma5" in frame.columns
    assert "volatility_pct" in frame.columns
    assert latest_snapshot(frame) is None


def test_latest_snapshot():
    frame = compute_indicators(bars_to_frame(make_bars(range(1, 71))))
    snapshot = latest_snapshot(frame)
    assert snapshot is not None
    assert snapshot.date == date(2026, 3, 11)
    assert snapshot.close == pytest.approx(70.0)
    assert snapshot.ma5 == pytest.approx(68.0)
    assert snapshot.trend == TREND_BULL
