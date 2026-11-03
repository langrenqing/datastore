import io
import zipfile
from datetime import date

from conftest import fixture_text

from goldtrack.events.gdelt import build_events, parse_export, parse_export_bytes


def sample_rows():
    return parse_export(fixture_text("gdelt_daily_sample.tsv"))


def test_parse_daily_layout():
    rows = sample_rows()
    assert len(rows) == 7
    row = rows[0]
    assert row.event_id == "1001"
    assert row.day == date(2026, 10, 30)
    assert row.is_root_event is True
    assert row.event_code == "190"
    assert row.root_code == "19"
    assert row.goldstein == -10.0
    assert row.num_sources == 30
    assert row.avg_tone == -9.0
    assert row.actor1_name == "IRAN"
    assert row.actor1_country == "IRN"
    assert row.action_country == "IR"
    assert row.action_fullname == "Tehran, Iran"
    assert row.lat == 35.7
    assert row.lon == 51.4
    assert row.url == "https://example.com/iran-strike"


def test_parse_skips_malformed_rows():
    rows = parse_export("bad\trow\n" + fixture_text("gdelt_daily_sample.tsv"))
    assert len(rows) == 7


def _field_row_2_0():
    fields = [""] * 61
    fields[0] = "2001"
    fields[1] = "20261102"
    fields[6] = "IRAN"
    fields[7] = "IRN"
    fields[25] = "1"
    fields[26] = "190"
    fields[27] = "190"
    fields[28] = "19"
    fields[29] = "4"
    fields[30] = "-10.0"
    fields[31] = "100"
    fields[32] = "20"
    fields[33] = "100"
    fields[34] = "-8.0"
    geo = 51
    fields[geo] = "1"
    fields[geo + 1] = "Tehran, Iran"
    fields[geo + 2] = "IR"
    fields[geo + 3] = "IR"
    fields[geo + 4] = ""
    fields[geo + 5] = "35.7"
    fields[geo + 6] = "51.4"
    fields[geo + 7] = "IR"
    fields[59] = "20261102120000"
    fields[60] = "https://example.com/live"
    return "\t".join(fields)


def test_parse_2_0_layout():
    rows = parse_export(_field_row_2_0())
    assert len(rows) == 1
    row = rows[0]
    assert row.event_id == "2001"
    assert row.action_country == "IR"
    assert row.action_fullname == "Tehran, Iran"
    assert row.lat == 35.7
    assert row.lon == 51.4
    assert row.url == "https://example.com/live"


def test_parse_export_bytes_reads_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("20261101.export.CSV", fixture_text("gdelt_daily_sample.tsv"))
    rows = parse_export_bytes(buffer.getvalue(), url="memory")
    assert len(rows) == 7


def test_build_events_filters_and_scores():
    events = build_events(sample_rows(), date(2026, 11, 2))
    by_id = {event.source_event_id: event for event in events}
    assert list(by_id) == ["1001", "1002", "1003", "1007"]

    strong = by_id["1001"]
    assert strong.region == "中东"
    assert strong.country == "伊朗"
    assert strong.event_type == "武装冲突"
    assert strong.level == 5
    assert strong.intensity > 90
    assert strong.type_severity == 30.0
    assert strong.scale_score == 20.0
    assert strong.tone_score == 9.0
    assert "伊朗" in strong.summary and "IRAN" in strong.summary

    protest = by_id["1002"]
    assert protest.region == "俄乌"
    assert protest.event_type == "抗议骚乱"
    assert protest.level == 2

    global_major = by_id["1003"]
    assert global_major.region == "其他"
    assert global_major.level >= 3

    assert by_id["1007"].region == "南亚"


def test_build_events_respects_thresholds():
    rows = sample_rows()
    only_strong = build_events(rows, date(2026, 11, 2), min_intensity=90)
    assert [event.source_event_id for event in only_strong] == ["1001"]

    fewer_sources = build_events(rows, date(2026, 11, 2), min_sources=5)
    assert "1002" not in {event.source_event_id for event in fewer_sources}
    assert "1003" in {event.source_event_id for event in fewer_sources}


def test_build_events_all_countries_keeps_diplomatic_friction():
    events = build_events(sample_rows(), date(2026, 11, 2), all_countries=True)
    assert "1006" in {event.source_event_id for event in events}
