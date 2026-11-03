import json
from datetime import date, timedelta

from conftest import fixture_text

from goldtrack.cli import main
from goldtrack.db import Database
from goldtrack.events.gdelt import build_events, parse_export
from goldtrack.events.models import GeoEvent
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar


def weekdays_ending(end, count):
    days = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def seed_prices(db_path, count=160):
    db = Database(db_path).init(INSTRUMENTS.values())
    days = weekdays_ending(date(2026, 11, 2), count)
    bars = []
    close = 3800.0
    for index, day in enumerate(days):
        close *= 1.0 + (0.004 if index % 5 else -0.003)
        bars.append(
            Bar(date=day, open=close - 2, high=close + 6, low=close - 6, close=close)
        )
    db.upsert_bars("XAU", bars, source="test")
    return db, days


def seed_fixture_events(db):
    rows = parse_export(fixture_text("gdelt_daily_sample.tsv"))
    db.upsert_events(build_events(rows, date(2026, 11, 2)))


def test_cli_events_fetch_offline(tmp_path, capsys, monkeypatch):
    from goldtrack.events import gdelt

    rows = parse_export(fixture_text("gdelt_daily_sample.tsv"))
    monkeypatch.setattr(gdelt, "fetch_export", lambda day, **kwargs: rows)
    db_path = tmp_path / "gold.db"

    code = main(
        [
            "--db",
            str(db_path),
            "events",
            "fetch",
            "--start",
            "2026-10-29",
            "--end",
            "2026-10-31",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "事件采集" in output
    assert "事件库当前共 4 条" in output
    db = Database(db_path)
    assert db.count_events() == 4


def test_cli_events_fetch_reports_failure(tmp_path, capsys, monkeypatch):
    from goldtrack.events import gdelt

    def broken(day, **kwargs):
        raise gdelt.GdeltError("模拟失败")

    monkeypatch.setattr(gdelt, "fetch_export", broken)
    code = main(
        [
            "--db",
            str(tmp_path / "gold.db"),
            "events",
            "fetch",
            "--start",
            "2026-10-30",
            "--end",
            "2026-10-30",
            "--retries",
            "1",
        ]
    )
    assert code == 1
    assert "模拟失败" in capsys.readouterr().out


def test_cli_events_query_json_and_market_date(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    db, _ = seed_prices(db_path)
    seed_fixture_events(db)
    assert main(
        [
            "--db",
            str(db_path),
            "events",
            "query",
            "--symbol",
            "XAU",
            "--start",
            "2026-10-30",
            "--end",
            "2026-11-01",
            "--json",
        ]
    ) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["source_event_id"] for record in records] == [
        "1007",
        "1002",
        "1003",
        "1001",
    ]
    by_id = {record["source_event_id"]: record for record in records}
    assert by_id["1001"]["market_date"] == "2026-10-30"
    assert by_id["1007"]["market_date"] == "2026-11-02"
    assert by_id["1001"]["region"] == "中东"
    assert by_id["1001"]["level"] == 5


def test_cli_events_query_filters_region(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    db, _ = seed_prices(db_path)
    seed_fixture_events(db)
    assert main(
        [
            "--db",
            str(db_path),
            "events",
            "query",
            "--start",
            "2026-10-30",
            "--end",
            "2026-11-01",
            "--region",
            "俄乌",
            "--json",
        ]
    ) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["source_event_id"] for record in records] == ["1002"]


def test_cli_events_windows_json(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    db, days = seed_prices(db_path)
    event_date = days[120]
    db.upsert_events(
        [
            GeoEvent(
                source="gdelt",
                source_event_id="window-event",
                occurred_at=event_date,
                published_at=event_date,
                country_code="IR",
                country="伊朗",
                region="中东",
                event_type="武装冲突",
                root_code="19",
                intensity=95.0,
                level=5,
            )
        ]
    )
    assert main(
        [
            "--db",
            str(db_path),
            "events",
            "windows",
            "--symbol",
            "XAU",
            "--days",
            "180",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_events"] == 1
    assert payload["computed_events"] == 1
    assert len(payload["impacts"]) == 3
    assert {row["window"] for row in payload["summary"]} == {"0,+1", "0,+3", "0,+5"}


def test_cli_events_chart(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    db, _ = seed_prices(db_path)
    seed_fixture_events(db)
    chart_dir = tmp_path / "charts"
    assert main(
        [
            "--db",
            str(db_path),
            "events",
            "chart",
            "--symbol",
            "XAU",
            "--days",
            "90",
            "--chart-dir",
            str(chart_dir),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "事件图表已生成" in output
    assert list(chart_dir.glob("events_XAU_*.png"))


def test_cli_events_on_empty_db(tmp_path, capsys):
    db_path = tmp_path / "empty.db"
    assert main(["--db", str(db_path), "events", "query"]) == 0
    assert "没有查询到事件" in capsys.readouterr().out

    assert main(["--db", str(db_path), "events", "chart"]) == 1
    assert "没有价格数据" in capsys.readouterr().out

    assert main(["--db", str(db_path), "events", "windows"]) == 1
    assert "没有价格数据" in capsys.readouterr().out
