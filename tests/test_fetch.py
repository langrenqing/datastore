from datetime import date

import goldtrack.fetch as fetch_module
from goldtrack.db import Database
from goldtrack.fetch import collect_instrument
from goldtrack.instruments import INSTRUMENTS
from goldtrack.models import Bar, Instrument
from goldtrack.sources.base import RetryPolicy, SourceError


def bar(day, close=100.0):
    return Bar(date=day, open=close, high=close + 1, low=close - 1, close=close)


class FakeSource:
    def __init__(self, outcomes, name="fake"):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.name = name

    def fetch(self, symbol):
        self.calls += 1
        index = min(self.calls - 1, len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def install_sources(monkeypatch, mapping):
    def fake_resolve(spec):
        source_name, symbol = spec.split(":", 1)
        return mapping[source_name], symbol

    monkeypatch.setattr(fetch_module, "resolve", fake_resolve)


def make_db(tmp_path):
    return Database(tmp_path / "gold.db").init(INSTRUMENTS.values())


def test_retry_then_success(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    bars = [bar(date(2026, 11, 2))]
    source = FakeSource([SourceError("boom"), SourceError("boom"), bars], name="a")
    install_sources(monkeypatch, {"a": source})
    instrument = Instrument("XAU", "X", "USD", "USD", sources=("a:XAU",))

    result = collect_instrument(
        db, instrument, full=True, retry=RetryPolicy(attempts=3, base_delay=0)
    )

    assert source.calls == 3
    assert result.source == "a:XAU"
    assert result.stored == 1
    assert db.count("XAU") == 1


def test_fallback_to_second_source(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    first = FakeSource([SourceError("down")], name="a")
    second = FakeSource([[bar(date(2026, 11, 2), 200.0)]], name="b")
    install_sources(monkeypatch, {"a": first, "b": second})
    instrument = Instrument("GC", "G", "USD", "USD", sources=("a:GC", "b:GC=F"))

    result = collect_instrument(
        db, instrument, full=True, retry=RetryPolicy(attempts=2, base_delay=0)
    )

    assert result.source == "b:GC=F"
    assert result.stored == 1
    assert any("a:GC" in message for message in result.errors)
    assert db.query("GC")[0].close == 200.0


def test_all_sources_fail(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    install_sources(
        monkeypatch,
        {"a": FakeSource([SourceError("down")], name="a"), "b": FakeSource([SourceError("also down")], name="b")},
    )
    instrument = Instrument("XAU", "X", "USD", "USD", sources=("a:XAU", "b:XAU"))

    result = collect_instrument(
        db, instrument, full=True, retry=RetryPolicy(attempts=2, base_delay=0)
    )

    assert result.source is None
    assert result.stored == 0
    assert len(result.errors) == 2
    assert db.count("XAU") == 0


def test_invalid_bars_are_skipped(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    invalid = Bar(date(2026, 11, 2), open=1.0, high=0.5, low=2.0, close=1.0)
    source = FakeSource([[invalid]], name="a")
    install_sources(monkeypatch, {"a": source})
    instrument = Instrument("XAU", "X", "USD", "USD", sources=("a:XAU",))

    result = collect_instrument(
        db, instrument, full=True, retry=RetryPolicy(attempts=1)
    )

    assert result.source is None
    assert result.skipped == 1
    assert db.count("XAU") == 0


def test_incremental_reuses_overlap_window(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    db.upsert_bars("XAU", [bar(date(2026, 11, 2))])
    source = FakeSource(
        [
            [
                bar(date(2026, 10, 1)),
                bar(date(2026, 10, 28)),
                bar(date(2026, 11, 2), 101.0),
            ]
        ],
        name="a",
    )
    install_sources(monkeypatch, {"a": source})
    instrument = Instrument("XAU", "X", "USD", "USD", sources=("a:XAU",))

    result = collect_instrument(
        db, instrument, retry=RetryPolicy(attempts=1), overlap_days=5
    )

    assert result.stored == 2
    assert db.query("XAU")[0].date == date(2026, 10, 28)
    assert db.latest_date("XAU") == date(2026, 11, 2)


def test_full_ignores_incremental_window(monkeypatch, tmp_path):
    db = make_db(tmp_path)
    db.upsert_bars("XAU", [bar(date(2026, 11, 2))])
    source = FakeSource([[bar(date(2026, 10, 1)), bar(date(2026, 11, 2))]], name="a")
    install_sources(monkeypatch, {"a": source})
    instrument = Instrument("XAU", "X", "USD", "USD", sources=("a:XAU",))

    result = collect_instrument(
        db, instrument, full=True, retry=RetryPolicy(attempts=1)
    )

    assert result.stored == 2
    assert db.count("XAU") == 2
