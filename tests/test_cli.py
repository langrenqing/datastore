import json
from datetime import date, timedelta

from goldtrack.cli import main
from goldtrack.db import Database
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar
from goldtrack.report import build_report


def seed(db_path):
    db = Database(db_path).init(INSTRUMENTS.values())
    bars = []
    for index in range(70):
        close = 3800 + index * 2
        bars.append(
            Bar(
                date=date(2026, 1, 1) + timedelta(days=index),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
            )
        )
    db.upsert_bars("XAU", bars, source="test")
    return db


def test_build_report_contains_instrument_and_trend(tmp_path):
    db = seed(tmp_path / "gold.db")
    report = build_report(db, ["XAU", "AU9999"])
    assert "黄金价格趋势跟踪" in report
    assert "XAU" in report
    assert "多头" in report
    assert "缺少数据：AU9999" in report


def test_cli_init_and_query_json(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    assert main(["--db", str(db_path), "init"]) == 0
    capsys.readouterr()

    seed(db_path)
    assert main(["--db", str(db_path), "query", "--symbol", "XAU", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 70
    assert records[-1]["date"] == "2026-03-11"
    assert records[-1]["close"] == 3938.0


def test_cli_indicators_json(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    seed(db_path)
    assert main(["--db", str(db_path), "indicators", "--symbol", "XAU", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert records[-1]["ma5"] == 3934.0
    assert records[-1]["trend"] == "多头"


def test_cli_report_and_sources(tmp_path, capsys):
    db_path = tmp_path / "gold.db"
    seed(db_path)
    assert main(["--db", str(db_path), "report", "--symbols", "XAU"]) == 0
    assert "XAU" in capsys.readouterr().out

    assert main(["sources"]) == 0
    output = capsys.readouterr().out
    assert "sina_global" in output
    assert "AU9999" in output
