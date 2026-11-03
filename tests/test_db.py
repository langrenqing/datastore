from datetime import date

from goldtrack.db import Database
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar


def make_bar(day, close):
    return Bar(date=day, open=close, high=close + 1, low=close - 1, close=close)


def test_init_and_upsert(tmp_path):
    db = Database(tmp_path / "gold.db").init(INSTRUMENTS.values())
    bars = [make_bar(date(2026, 10, 30), 3900.0), make_bar(date(2026, 11, 2), 3890.0)]
    assert db.upsert_bars("XAU", bars, source="test") == 2
    assert db.count("XAU") == 2
    assert db.latest_date("XAU") == date(2026, 11, 2)
    assert db.symbols() == ["XAU"]


def test_upsert_updates_existing_date(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    db.upsert_bars("XAU", [make_bar(date(2026, 11, 2), 3890.0)])
    db.upsert_bars("XAU", [make_bar(date(2026, 11, 2), 3902.0)])
    assert db.count("XAU") == 1
    assert db.query("XAU")[0].close == 3902.0


def test_query_range_is_inclusive(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    db.upsert_bars(
        "XAU",
        [
            make_bar(date(2026, 10, 28), 1.0),
            make_bar(date(2026, 10, 29), 2.0),
            make_bar(date(2026, 10, 30), 3.0),
            make_bar(date(2026, 11, 2), 4.0),
        ],
    )
    bars = db.query("XAU", start=date(2026, 10, 29), end="2026-10-30")
    assert [bar.date for bar in bars] == [date(2026, 10, 29), date(2026, 10, 30)]
    assert db.query("XAU", start=date(2026, 10, 31))[0].date == date(2026, 11, 2)


def test_instrument_metadata(tmp_path):
    db = Database(tmp_path / "gold.db").init(INSTRUMENTS.values())
    instrument = db.instrument("AU9999")
    assert instrument is not None
    assert instrument.name == "上海金 Au99.99"
    assert instrument.unit == "元/克"
    assert db.instrument("NOPE") is None


def test_connect_enables_wal_and_busy_timeout(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        # 1 == NORMAL
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_init_drops_redundant_prices_index(tmp_path):
    db = Database(tmp_path / "gold.db").init()
    with db.connect() as conn:
        conn.execute("CREATE INDEX idx_prices_symbol_date ON prices(symbol, date)")

    db.init()

    with db.connect() as conn:
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'prices'"
            )
        }
    assert "idx_prices_symbol_date" not in indexes
