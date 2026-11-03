from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from conftest import fixture_text

from goldtrack.cli import main
from goldtrack.db import CollectionBusy, Database
from goldtrack.events.gdelt import build_events, parse_export
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar, FetchResult
from goldtrack.runs import run_events, run_prices
from goldtrack.web.jobs import refresh_run


def make_bar(day: date, close: float) -> Bar:
    return Bar(date=day, open=close, high=close + 2, low=close - 2, close=close)


def test_run_lifecycle_and_single_flight(tmp_path):
    db = Database(tmp_path / "gold.db").init(INSTRUMENTS.values())
    run_id = db.create_run("prices", {"symbols": ["XAU"]}, source="cli")
    assert db.active_run()["id"] == run_id

    with pytest.raises(CollectionBusy):
        db.create_run("events", {}, source="web")

    db.start_run(run_id, pid=4321)
    db.set_run_progress(run_id, 1, 2, "XAU 完成")
    run = db.get_run(run_id)
    assert run["status"] == "running"
    assert run["pid"] == 4321
    assert run["progress_current"] == 1

    db.finish_run(run_id, "partial", summary={"results": [{"symbol": "XAU"}]})
    run = db.get_run(run_id)
    assert run["status"] == "partial"
    assert run["finished_at"]
    assert json.loads(run["summary"])["results"][0]["symbol"] == "XAU"
    assert db.active_run() is None


def test_refresh_run_interrupts_dead_process(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    run_id = db.create_run("prices", {}, source="web")
    with db.connect() as conn:
        conn.execute(
            "UPDATE collection_runs SET pid = 999999, started_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", run_id),
        )
    run = refresh_run(db, db.get_run(run_id))
    assert run["status"] == "interrupted"


def test_refresh_run_keeps_recent_queued_run(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    run_id = db.create_run("prices", {}, source="web")
    run = refresh_run(db, db.get_run(run_id))
    assert run["status"] == "queued"


def test_run_prices_records_status_and_progress(tmp_path, monkeypatch):
    from goldtrack import runs as runs_module

    db = Database(tmp_path / "gold.db").init(INSTRUMENTS.values())
    day = date(2026, 11, 2)
    result = FetchResult(
        symbol="XAU",
        source="sina_global:XAU",
        bars=[make_bar(day, 3900.0)],
        stored=1,
    )

    def fake_collect_all(db_arg, symbols, **kwargs):
        callback = kwargs.get("on_symbol")
        if callback is not None:
            callback(1, len(symbols), result)
        return [result]

    monkeypatch.setattr(runs_module, "collect_all", fake_collect_all)
    run_id = db.create_run("prices", {"symbols": ["XAU"]}, source="web")
    results = run_prices(db, run_id, symbols=["XAU"])

    assert results == [result]
    run = db.get_run(run_id)
    assert run["status"] == "succeeded"
    assert run["progress_current"] == 1
    assert json.loads(run["summary"])["results"][0]["stored"] == 1


def test_run_events_records_status(tmp_path, monkeypatch):
    from goldtrack.events import gdelt

    rows = parse_export(fixture_text("gdelt_daily_sample.tsv"))
    monkeypatch.setattr(gdelt, "fetch_export", lambda day, **kwargs: rows)
    db = Database(tmp_path / "gold.db").init(INSTRUMENTS.values())
    run_id = db.create_run("events", {}, source="web")
    summary = run_events(
        db,
        run_id,
        start=date(2026, 10, 29),
        end=date(2026, 10, 31),
    )
    run = db.get_run(run_id)
    assert summary.days == 3
    assert run["status"] == "succeeded"
    assert run["progress_current"] == 3
    assert json.loads(run["summary"])["stored"] == 12


def test_cli_events_fetch_can_record_run(tmp_path, capsys, monkeypatch):
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
            "--record-run",
        ]
    )
    assert code == 0
    runs = Database(db_path).list_runs()
    assert len(runs) == 1
    assert runs[0]["source"] == "cli"
    assert runs[0]["status"] == "succeeded"
    assert json.loads(runs[0]["summary"])["days"] == 3
