from datetime import date, timedelta
from pathlib import Path

import pytest

from goldtrack.events.chart import default_event_chart_path, plot_event_chart
from goldtrack.events.models import GeoEvent
from goldtrack.indicators import bars_to_frame, compute_indicators
from goldtrack.instruments import get_instrument
from goldtrack.models import Bar


def weekdays(count, start=date(2026, 1, 1)):
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def make_frame(count=120):
    days = weekdays(count)
    bars = []
    for index, day in enumerate(days):
        close = 3800 + index * 2 + (index % 11) * 3
        bars.append(
            Bar(date=day, open=close - 2, high=close + 5, low=close - 6, close=close)
        )
    return days, compute_indicators(bars_to_frame(bars))


def make_events(days):
    types = ["武装冲突", "胁迫/制裁", "抗议骚乱", "军事部署"]
    regions = [("中东", "IR", "伊朗"), ("俄乌", "UP", "乌克兰"), ("南亚", "PK", "巴基斯坦")]
    events = []
    for index, day in enumerate(days[40:110:7]):
        region, code, name = regions[index % len(regions)]
        events.append(
            GeoEvent(
                source="gdelt",
                source_event_id=f"e{index}",
                occurred_at=day,
                published_at=day,
                country_code=code,
                country=name,
                region=region,
                event_type=types[index % len(types)],
                root_code="19",
                intensity=45 + index * 3,
                level=min(5, 2 + index // 3),
                summary=f"{name}：{types[index % len(types)]}",
            )
        )
    return events


def test_plot_event_chart_writes_png(tmp_path):
    days, frame = make_frame()
    out = tmp_path / "charts" / "events_XAU.png"
    path = plot_event_chart(frame, make_events(days), get_instrument("XAU"), out, days=90)
    assert path == out
    assert out.exists()
    assert out.stat().st_size > 10_000


def test_plot_event_chart_handles_weekend_alignment(tmp_path):
    days, frame = make_frame()
    saturday = date(2026, 5, 2)
    event = GeoEvent(
        source="gdelt",
        source_event_id="weekend",
        occurred_at=saturday,
        published_at=saturday,
        country_code="IR",
        country="伊朗",
        region="中东",
        event_type="武装冲突",
        root_code="19",
        intensity=90.0,
        level=5,
    )
    out = tmp_path / "weekend.png"
    assert plot_event_chart(frame, [event], get_instrument("XAU"), out, days=120).exists()


def test_plot_event_chart_rejects_empty_inputs(tmp_path):
    days, frame = make_frame()
    with pytest.raises(ValueError):
        plot_event_chart(frame, [], get_instrument("XAU"), tmp_path / "x.png")
    with pytest.raises(ValueError):
        plot_event_chart(
            compute_indicators(bars_to_frame([])),
            make_events(days),
            get_instrument("XAU"),
            tmp_path / "x.png",
        )


def test_default_event_chart_path():
    path = default_event_chart_path(
        Path("/tmp/charts"), "XAU", date(2026, 11, 2), 180
    )
    assert str(path).endswith("events_XAU_2026-11-02_180d.png")
