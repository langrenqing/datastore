from datetime import date, timedelta

import pytest

from goldtrack.events.alignment import align_events, align_market_date, trading_days
from goldtrack.events.analysis import compute_event_impacts, summarize_impacts
from goldtrack.events.models import GeoEvent
from goldtrack.models import Bar


def weekdays(count, start=date(2026, 1, 1)):
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def make_bars(count=160, event_index=None, event_horizon=5):
    days = weekdays(count)
    closes = []
    close = 100.0
    for index in range(count):
        if event_index is not None and event_index <= index <= event_index + event_horizon:
            close *= 1.01
        closes.append(close)
    bars = [
        Bar(
            date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=0.0,
        )
        for day, close in zip(days, closes)
    ]
    return days, bars


def make_event(occurred_at, level=5, intensity=90.0, event_id="e1"):
    return GeoEvent(
        source="gdelt",
        source_event_id=event_id,
        occurred_at=occurred_at,
        published_at=occurred_at,
        country_code="IR",
        country="伊朗",
        region="中东",
        event_type="武装冲突",
        root_code="19",
        level=level,
        intensity=intensity,
    )


def test_align_weekend_event_rolls_forward():
    days = [date(2026, 10, 30), date(2026, 11, 2)]
    saturday = date(2026, 10, 31)
    assert align_market_date(saturday, days) == date(2026, 11, 2)
    assert align_market_date(date(2026, 11, 3), days) is None
    assert trading_days([days[1], days[0], days[1]]) == days


def test_align_events_drops_unpriced_events():
    days = [date(2026, 10, 30), date(2026, 11, 2)]
    events = [make_event(date(2026, 10, 31)), make_event(date(2026, 11, 3), event_id="e2")]
    aligned = align_events(events, days)
    assert len(aligned) == 1
    assert aligned[0][1] == date(2026, 11, 2)


def test_compute_impacts_uses_trading_day_windows():
    days, bars = make_bars(event_index=127)
    event_date = days[127]
    assert event_date.weekday() == 0  # Monday, so the Saturday event aligns to it
    event = make_event(event_date - timedelta(days=2))
    impacts = compute_event_impacts([event], bars, "XAU")
    by_window = {impact.window: impact for impact in impacts}
    assert set(by_window) == {"0,+1", "0,+3", "0,+5"}
    assert by_window["0,+1"].car_pct == pytest.approx(2.0)
    assert by_window["0,+3"].car_pct == pytest.approx(4.0)
    assert by_window["0,+5"].car_pct == pytest.approx(6.0)
    assert by_window["0,+5"].baseline_pct == pytest.approx(0.0)
    assert by_window["0,+5"].market_date == event_date
    assert by_window["0,+5"].est_end < event_date


def test_compute_impacts_skips_events_without_history_or_future():
    days, bars = make_bars(event_index=130)
    too_early = make_event(days[10], event_id="early")
    after_last = make_event(days[-1] + timedelta(days=3), event_id="future")
    assert compute_event_impacts([too_early, after_last], bars, "XAU") == []


def test_compute_impacts_skips_incomplete_forward_window():
    days, bars = make_bars(event_index=127)
    event = make_event(days[-1])
    assert compute_event_impacts([event], bars, "XAU") == []


def test_summarize_impacts_by_window_and_level():
    days, bars = make_bars(event_index=130)
    events = [
        make_event(days[129], level=5, intensity=95.0, event_id="a"),
        make_event(days[130], level=1, intensity=35.0, event_id="b"),
    ]
    # Put both events on the same Monday market date.
    events[0] = make_event(days[128], level=5, intensity=95.0, event_id="a")
    impacts = compute_event_impacts(events, bars, "XAU")
    summary = summarize_impacts(impacts, events)
    overall = [row for row in summary if row.level is None]
    assert len(overall) == 3
    assert all(row.count == 2 for row in overall)
    levels = {row.level for row in summary}
    assert {5, 1, None} <= levels
    assert all(0.0 <= row.positive_share <= 1.0 for row in summary)
