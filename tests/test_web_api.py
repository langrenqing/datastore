from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from goldtrack.db import Database
from goldtrack.events.models import GeoEvent
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar
from goldtrack.web import app as web_app
from goldtrack.web.app import create_app


def weekdays_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def seed_db(path, bars: int = 260, events: int = 240) -> Database:
    db = Database(path).init(INSTRUMENTS.values())
    days = weekdays_ending(date(2026, 11, 2), bars)
    rows = []
    for index, day in enumerate(days):
        close = 3800.0 + index * 1.5 + math.sin(index / 9) * 40
        rows.append(
            Bar(
                date=day,
                open=close - 3,
                high=close + 7,
                low=close - 7,
                close=close,
                volume=1000 + index,
            )
        )
    db.upsert_bars("XAU", rows, source="test")

    regions = ["中东", "俄乌", "台海", "朝鲜半岛"]
    types = ["武装冲突", "威胁", "军事部署", "抗议骚乱"]
    event_rows = []
    for index in range(events):
        day = date(2026, 9, 1) + timedelta(days=index % 61)
        event_rows.append(
            GeoEvent(
                source="gdelt",
                source_event_id=f"evt-{index}",
                occurred_at=day,
                published_at=day,
                country_code="IR",
                country="伊朗" if index % 2 else "俄罗斯",
                region=regions[index % len(regions)],
                event_type=types[index % len(types)],
                root_code="19",
                intensity=40.0 + (index % 55),
                level=1 + (index % 5),
                num_sources=3 + (index % 6),
                summary=f"测试事件 {index}",
                url=f"https://example.com/events/{index}",
            )
        )
    db.upsert_events(event_rows)
    return db


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "web.db"
    seed_db(db_path)
    app = create_app(db_path)
    with TestClient(app) as test_client:
        yield test_client


def fake_spawner(db, payload, db_path, source="web") -> int:
    params = {key: value for key, value in payload.items() if key != "kind"}
    return db.create_run(payload["kind"], params=params, source=source)


def test_meta_and_instruments(client):
    meta = client.get("/api/meta").json()
    assert meta["today"]
    assert len(meta["instruments"]) == 4
    assert "中东" in meta["event_regions"]
    assert any(item["name"] == "武装冲突" for item in meta["event_types"])
    assert meta["defaults"]["page_size"] == 50

    payload = client.get("/api/instruments").json()
    by_symbol = {item["symbol"]: item for item in payload["instruments"]}
    assert by_symbol["XAU"]["latest_date"] == "2026-11-02"
    assert by_symbol["XAU"]["count"] == 260
    assert by_symbol["AU9999"]["latest_date"] is None
    assert by_symbol["XAU"]["latest_close"] is not None


def test_prices_endpoint_returns_indicators_and_range(client):
    payload = client.get("/api/prices?symbol=XAU&days=30").json()
    assert payload["count"] == 30
    assert payload["total_count"] == 260
    assert payload["start"] == payload["bars"][0]["date"]
    assert payload["end"] == "2026-11-02"
    last = payload["bars"][-1]
    for key in ("open", "high", "low", "close", "ma5", "ma20", "ma60", "volatility_pct", "trend"):
        assert key in last
    assert last["ma5"] is not None
    assert last["trend"] in ("多头", "空头", "震荡", "数据不足")

    ranged = client.get(
        "/api/prices?symbol=XAU&start=2026-10-01&end=2026-10-31"
    ).json()
    assert ranged["bars"]
    assert all("2026-10-01" <= item["date"] <= "2026-10-31" for item in ranged["bars"])

    reduced = client.get("/api/prices?symbol=XAU&days=0&max_points=50").json()
    assert reduced["downsampled"] is True
    assert 50 <= reduced["count"] <= 55
    assert reduced["bars"][-1]["date"] == "2026-11-02"


def test_prices_unknown_symbol_is_404(client):
    response = client.get("/api/prices?symbol=NOPE")
    assert response.status_code == 404


def test_events_pagination_and_filters(client):
    first = client.get("/api/events?start=2026-09-01&end=2026-10-31&limit=10&offset=0").json()
    second = client.get("/api/events?start=2026-09-01&end=2026-10-31&limit=10&offset=10").json()
    assert first["total"] == second["total"] > 10
    assert len(first["events"]) == 10
    ids_first = {item["source_event_id"] for item in first["events"]}
    ids_second = {item["source_event_id"] for item in second["events"]}
    assert ids_first.isdisjoint(ids_second)
    assert first["events"][0]["market_date"] is not None
    assert "raw_json" not in first["events"][0]

    region = client.get(
        "/api/events?start=2026-09-01&end=2026-10-31&region=中东&limit=200"
    ).json()
    assert region["events"]
    assert {item["region"] for item in region["events"]} == {"中东"}

    level = client.get(
        "/api/events?start=2026-09-01&end=2026-10-31&min_level=4&limit=200"
    ).json()
    assert level["events"]
    assert all(item["level"] >= 4 for item in level["events"])

    keyword = client.get(
        "/api/events?start=2026-09-01&end=2026-10-31&q=测试事件 7&limit=200"
    ).json()
    assert keyword["events"]
    assert all("测试事件 7" in item["summary"] for item in keyword["events"])


def test_overlay_aggregates_per_day_and_region(client):
    payload = client.get("/api/events/overlay?symbol=XAU&days=90").json()
    assert payload["start"]
    assert payload["end"] == "2026-11-02"
    assert len(payload["price"]) == 90
    assert payload["event_count"] > 0
    market_dates = [item["market_date"] for item in payload["events"]]
    assert len(market_dates) == len(set(market_dates))
    assert len(payload["top"]) <= 5
    assert payload["regions"]
    assert all(item["region"] in payload["regions"] for item in payload["timeline"])

    # one strongest event per (region, trading day)
    keys = [(item["region"], item["market_date"]) for item in payload["timeline"]]
    assert len(keys) == len(set(keys))


def test_status_reports_data_and_runs(client):
    payload = client.get("/api/status").json()
    by_symbol = {item["symbol"]: item for item in payload["instruments"]}
    assert by_symbol["XAU"]["latest_date"] == "2026-11-02"
    assert payload["events"]["count"] == 240
    assert payload["events"]["latest_date"]
    assert payload["active_run"] is None
    assert payload["runs"] == []


def test_status_last_run_is_per_instrument(client):
    db = client.app.state.db
    run_id = db.create_run("prices", {"symbols": ["XAU"]}, source="web")
    db.finish_run(
        run_id,
        "succeeded",
        summary={
            "results": [
                {
                    "symbol": "XAU",
                    "source": "sina_global:XAU",
                    "stored": 3,
                    "errors": [],
                }
            ]
        },
    )
    payload = client.get("/api/status").json()
    by_symbol = {item["symbol"]: item for item in payload["instruments"]}
    assert by_symbol["XAU"]["last_success"]["id"] == run_id
    assert by_symbol["AU9999"]["last_success"] is None
    assert by_symbol["AU9999"]["last_failure"] is None


def test_collect_requires_no_active_run_and_validates_payload(client):
    client.app.dependency_overrides[web_app.get_spawner] = lambda: fake_spawner

    bad_symbol = client.post(
        "/api/collect", json={"kind": "prices", "symbols": ["NOPE"]}
    )
    assert bad_symbol.status_code == 422

    bad_range = client.post(
        "/api/collect",
        json={
            "kind": "events",
            "start": "2026-10-10",
            "end": "2026-10-01",
        },
    )
    assert bad_range.status_code == 422

    started = client.post(
        "/api/collect",
        json={"kind": "prices", "symbols": ["XAU"], "full": False},
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    job = client.get(f"/api/jobs/{run_id}").json()
    assert job["kind"] == "prices"
    assert job["status"] == "queued"

    conflict = client.post(
        "/api/collect", json={"kind": "events", "days": 30}
    )
    assert conflict.status_code == 409

    client.app.state.db.finish_run(run_id, "succeeded", summary={"results": []})
    free = client.post("/api/collect", json={"kind": "events", "days": 30})
    assert free.status_code == 202


def test_job_404_and_unknown_collect_kind(client):
    assert client.get("/api/jobs/9999").status_code == 404
    response = client.post("/api/collect", json={"kind": "other"})
    assert response.status_code == 422
