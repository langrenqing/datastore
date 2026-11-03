from datetime import date

from goldtrack.db import Database
from goldtrack.sources.base import RetryPolicy
from goldtrack.events.fetch import collect_events, resolve_range
from goldtrack.events.gdelt import GdeltError, GdeltPermanentError
from goldtrack.events.models import GeoEvent


def make_event(day, event_id="e1"):
    return GeoEvent(
        source="gdelt",
        source_event_id=event_id,
        occurred_at=day,
        published_at=day,
        country_code="IR",
        country="伊朗",
        region="中东",
        event_type="武装冲突",
        root_code="19",
        intensity=85.0,
        level=4,
    )


def test_resolve_range_uses_backfill_days_on_empty_db(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    start, end = resolve_range(db, start=None, end=None, days=30)
    assert end == date(2026, 11, 1)
    assert start == date(2026, 10, 3)


def test_resolve_range_resumes_with_overlap(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    db.upsert_events([make_event(date(2026, 10, 28))])
    start, end = resolve_range(db, start=None, end=None, days=30)
    assert start == date(2026, 10, 27)
    assert end == date(2026, 11, 1)


def test_resolve_range_clips_future_end(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    start, end = resolve_range(
        db, start=date(2026, 10, 1), end=date(2026, 11, 2), days=30
    )
    assert start == date(2026, 10, 1)
    assert end == date(2026, 11, 1)


def test_collect_events_calls_each_day_and_records_failure(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    calls = []

    def fake_fetch(day):
        calls.append(day)
        if day == date(2026, 10, 31):
            raise RuntimeError("boom")
        return []

    summary = collect_events(
        db,
        start=date(2026, 10, 30),
        end=date(2026, 11, 1),
        retry=RetryPolicy(attempts=1),
        fetch_day=fake_fetch,
    )
    assert calls == [date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)]
    assert summary.days == 3
    assert len(summary.failures) == 1
    assert summary.failures[0].day == date(2026, 10, 31)
    assert "boom" in summary.failures[0].error


def test_collect_events_does_not_retry_permanent_errors(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    calls = []
    sleeps = []
    day = date(2026, 10, 30)

    def fake_fetch(requested):
        calls.append(requested)
        raise GdeltPermanentError("missing")

    summary = collect_events(
        db,
        start=day,
        end=day,
        retry=RetryPolicy(attempts=3, base_delay=1.0),
        fetch_day=fake_fetch,
        sleep=sleeps.append,
    )

    assert calls == [day]
    assert sleeps == []
    assert len(summary.failures) == 1
    assert "missing" in summary.failures[0].error


def test_collect_events_retries_transient_errors(tmp_path, monkeypatch):
    from goldtrack.events import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_yesterday_utc", lambda: date(2026, 11, 1))
    db = Database(tmp_path / "gold.db").init()
    calls = []
    sleeps = []
    day = date(2026, 10, 30)

    def fake_fetch(requested):
        calls.append(requested)
        raise GdeltError("temporary")

    summary = collect_events(
        db,
        start=day,
        end=day,
        retry=RetryPolicy(attempts=3, base_delay=1.0),
        fetch_day=fake_fetch,
        sleep=sleeps.append,
    )

    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert len(summary.failures) == 1
    assert "temporary" in summary.failures[0].error
