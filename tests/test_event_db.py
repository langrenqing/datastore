from datetime import date

from conftest import fixture_text

from goldtrack.db import Database
from goldtrack.events.gdelt import build_events, parse_export
from goldtrack.events.models import EventImpact


def make_events():
    rows = parse_export(fixture_text("gdelt_daily_sample.tsv"))
    return build_events(rows, date(2026, 11, 2))


def test_upsert_events_is_idempotent(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    events = make_events()
    assert db.upsert_events(events) == 4
    assert db.count_events() == 4
    assert db.upsert_events(events) == 4
    assert db.count_events() == 4


def test_query_events_filters(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    db.upsert_events(make_events())

    assert [event.source_event_id for event in db.query_events()] == [
        "1001",
        "1003",
        "1002",
        "1007",
    ]
    assert [event.source_event_id for event in db.query_events(regions=["俄乌"])] == ["1002"]
    assert [event.source_event_id for event in db.query_events(min_level=5)] == ["1001"]
    assert [event.source_event_id for event in db.query_events(min_intensity=80)] == ["1001"]
    assert [
        event.source_event_id
        for event in db.query_events(start=date(2026, 11, 1), end=date(2026, 11, 1))
    ] == ["1002", "1007"]
    assert [
        event.source_event_id for event in db.query_events(limit=1)
    ] == ["1007"]


def test_latest_event_source_date_and_range(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    assert db.latest_event_published_at() is None
    assert db.event_date_range() == (None, None)
    db.upsert_events(make_events())
    assert db.latest_event_published_at() == date(2026, 11, 2)
    assert db.event_date_range() == (date(2026, 10, 30), date(2026, 11, 1))


def test_event_impact_roundtrip(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    impact = EventImpact(
        source="gdelt",
        source_event_id="1001",
        symbol="XAU",
        window="0,+5",
        market_date=date(2026, 11, 2),
        est_start=date(2026, 5, 1),
        est_end=date(2026, 10, 1),
        baseline_pct=0.05,
        car_pct=1.25,
        computed_at="2026-11-02T00:00:00+00:00",
    )
    assert db.upsert_event_impacts([impact]) == 1
    stored = db.query_event_impacts("XAU")
    assert len(stored) == 1
    assert stored[0].car_pct == 1.25
    assert stored[0].window == "0,+5"

    updated = EventImpact(**{**impact.__dict__, "car_pct": 2.5})
    db.upsert_event_impacts([updated])
    assert db.query_event_impacts("XAU")[0].car_pct == 2.5


def test_read_commands_create_event_tables_on_empty_db(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    assert db.query_events() == []
    assert db.count_events() == 0
