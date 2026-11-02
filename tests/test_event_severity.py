
from goldtrack.events.severity import (
    event_type,
    intensity_score,
    level_for,
    scale_score,
    spread_score,
    tone_score,
    type_severity,
)
from goldtrack.events.watchlist import (
    ALL_REGIONS,
    country_name,
    country_region,
    is_watchlisted,
)


def test_cameo_type_mapping():
    assert event_type("19") == "武装冲突"
    assert event_type("20") == "大规模暴力"
    assert event_type("18") == "暴力袭击"
    assert event_type("17") == "胁迫/制裁"
    assert event_type("14") == "抗议骚乱"
    assert event_type("01") == "其他"


def test_component_maximums_sum_to_100():
    severity, scale, spread, tone, region, total = intensity_score(
        root_code="19",
        goldstein=-10,
        num_sources=50,
        avg_tone=-10,
        region_weight=15,
    )
    assert severity == 30.0
    assert scale == 20.0
    assert spread == 25.0
    assert tone == 10.0
    assert region == 15.0
    assert total == 100.0


def test_components_are_capped_and_signed():
    assert scale_score(-20) == 20.0
    assert scale_score(20) == 20.0
    assert scale_score(None) == 0.0
    assert spread_score(0) == 0.0
    assert spread_score(1000) == 25.0
    assert tone_score(5) == 0.0
    assert tone_score(-20) == 10.0
    assert type_severity("14") < type_severity("19")


def test_level_thresholds():
    assert level_for(100) == 5
    assert level_for(90) == 5
    assert level_for(89.9) == 4
    assert level_for(75) == 4
    assert level_for(74.9) == 3
    assert level_for(60) == 3
    assert level_for(59.9) == 2
    assert level_for(40) == 2
    assert level_for(39.9) == 1


def test_country_regions_use_fips_codes():
    assert country_region("IR") == "中东"
    assert country_region("RS") == "俄乌"
    assert country_region("BO") == "俄乌"
    assert country_region("TW") == "台海"
    assert country_region("NI") == "其他"  # Nigeria is NI, not NG (Niger)
    assert is_watchlisted("UP") is True
    assert is_watchlisted("US") is False
    assert country_name("IR") == "伊朗"
    assert country_name("US") == "美国"
    assert country_name("AU") == "澳大利亚"
    assert country_name("", "Somewhere, Elsewhere") == "Elsewhere"
    assert country_name("ZZ", "Somewhere, Elsewhere") == "ZZ"
    assert "其他" in ALL_REGIONS
