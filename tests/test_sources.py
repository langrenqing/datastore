from datetime import date

import pytest

from conftest import fixture_text
from goldtrack.sources.base import SourceError
from goldtrack.sources.sge import parse_sge_payload
from goldtrack.sources.sina import (
    parse_domestic_rows,
    parse_global_rows,
    parse_sina_jsonp,
)
from goldtrack.sources.yahoo import parse_yahoo_payload


def test_parse_global_rows():
    rows = parse_sina_jsonp(fixture_text("sina_global_xau.jsonp"))
    bars = parse_global_rows(rows)
    assert [bar.date for bar in bars] == [
        date(2026, 10, 29),
        date(2026, 10, 30),
        date(2026, 11, 2),
    ]
    assert bars[-1].open == pytest.approx(3881.0)
    assert bars[-1].high == pytest.approx(3919.0)
    assert bars[-1].low == pytest.approx(3875.0)
    assert bars[-1].close == pytest.approx(3902.0)


def test_parse_domestic_rows():
    rows = parse_sina_jsonp(fixture_text("sina_domestic_au0.jsonp"))
    bars = parse_domestic_rows(rows)
    assert len(bars) == 3
    last = bars[-1]
    assert last.date == date(2026, 11, 2)
    assert last.open == pytest.approx(851.0)
    assert last.high == pytest.approx(855.0)
    assert last.low == pytest.approx(833.0)
    assert last.close == pytest.approx(836.1)
    assert last.volume == pytest.approx(150000)


def test_parse_sge_payload_row_order():
    payload = __import__("json").loads(fixture_text("sge_au9999.json"))
    bars = parse_sge_payload(payload)
    assert len(bars) == 3
    last = bars[-1]
    # SGE row order is [date, open, close, low, high].
    assert last.date == date(2026, 11, 2)
    assert last.open == pytest.approx(851.0)
    assert last.close == pytest.approx(836.08)
    assert last.low == pytest.approx(833.0)
    assert last.high == pytest.approx(855.0)
    assert last.volume == 0.0


def test_parse_sge_rejects_empty_payload():
    with pytest.raises(SourceError):
        parse_sge_payload({"time": []})
    with pytest.raises(SourceError):
        parse_sge_payload({})


def test_parse_yahoo_payload_skips_null_close():
    import json

    payload = json.loads(fixture_text("yahoo_gc.json"))
    bars = parse_yahoo_payload(payload)
    assert [bar.date for bar in bars] == [date(2025, 1, 1), date(2025, 1, 2)]
    assert bars[0].close == pytest.approx(2635.0)
    assert bars[1].volume == pytest.approx(15000)


def test_parse_sina_jsonp_rejects_null():
    with pytest.raises(SourceError):
        parse_sina_jsonp("var _XAU=(null);")
    with pytest.raises(SourceError):
        parse_sina_jsonp("not a jsonp body")


def test_source_rows_skip_invalid_close():
    rows = [
        {"date": "2026-11-02", "open": "1", "high": "2", "low": "0.5", "close": "0"},
        {"date": "2026-11-03", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
    ]
    bars = parse_global_rows(rows)
    assert len(bars) == 1
    assert bars[0].date == date(2026, 11, 3)
