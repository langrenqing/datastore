from datetime import date, timedelta

import pytest

from goldtrack.chart import plot_chart
from goldtrack.indicators import bars_to_frame, compute_indicators
from goldtrack.instruments import get_instrument
from goldtrack.models import Bar


def make_bars(count=80):
    start = date(2026, 1, 1)
    bars = []
    for index in range(count):
        close = 3800 + index * 3 + (index % 7) * 2
        bars.append(
            Bar(
                date=start + timedelta(days=index),
                open=close - 2,
                high=close + 4,
                low=close - 5,
                close=close,
                volume=1000 + index,
            )
        )
    return bars


def test_plot_chart_writes_png(tmp_path):
    bars = make_bars()
    frame = compute_indicators(bars_to_frame(bars))
    out = tmp_path / "charts" / "XAU.png"

    path = plot_chart(frame, get_instrument("XAU"), out, days=60)

    assert path == out
    assert out.exists()
    assert out.stat().st_size > 10_000


def test_plot_chart_rejects_empty_frame(tmp_path):
    with pytest.raises(ValueError):
        plot_chart(bars_to_frame([]), get_instrument("XAU"), tmp_path / "x.png")
