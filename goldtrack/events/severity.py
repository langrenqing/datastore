"""CAMEO event categories and the rule-based intensity score.

The score is intentionally transparent rather than a black box.  It combines
five components that add up to 100 points:

===============  =====  ==============================================
Component        Range  Meaning
===============  =====  ==============================================
type_severity      30   CAMEO root-code severity (war > terror > protest)
scale_score        20   ``|GoldsteinScale| / 10`` (event impact direction)
spread_score       25   ``log1p(NumSources)`` with a cap
tone_score         10   how negative the average article tone is
region_weight      15   static market relevance of the action region
===============  =====  ==============================================

Escalation measured against a trailing baseline needs a longer clean history
and event de-duplication, so it belongs to phase two rather than this rule
score.  The components are stored next to the final score for later analysis.
"""

from __future__ import annotations

import math

# CAMEO root codes that describe coercive or violent events.  Routine
# statements (01-09) and diplomatic cooperation are not collected.
SIGNIFICANT_ROOTS: frozenset[str] = frozenset(
    {"10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"}
)

ROOT_TYPES: dict[str, str] = {
    "20": "大规模暴力",
    "19": "武装冲突",
    "18": "暴力袭击",
    "17": "胁迫/制裁",
    "16": "关系降级",
    "15": "军事部署",
    "14": "抗议骚乱",
    "13": "威胁",
    "12": "拒绝",
    "11": "谴责",
    "10": "外交要求",
}

# How severe a CAMEO root category is, independent of its scale (0-30).
ROOT_SEVERITY: dict[str, float] = {
    "20": 30.0,
    "19": 30.0,
    "18": 27.0,
    "17": 24.0,
    "15": 21.0,
    "13": 18.0,
    "16": 18.0,
    "14": 15.0,
    "10": 9.0,
    "11": 9.0,
    "12": 9.0,
}

# Colour palette shared by the chart and any future reports.
TYPE_COLORS: dict[str, str] = {
    "武装冲突": "#c0392b",
    "大规模暴力": "#7b241c",
    "暴力袭击": "#e74c3c",
    "胁迫/制裁": "#e67e22",
    "军事部署": "#8e44ad",
    "抗议骚乱": "#2980b9",
    "威胁": "#d35400",
    "关系降级": "#7f8c8d",
    "外交要求": "#95a5a6",
    "谴责": "#bdc3c7",
    "拒绝": "#bdc3c7",
    "其他": "#d5d8dc",
}

# Intensity bands used for the 1-5 display level.  Thresholds are derived from
# the component maximums above (major war events land at 90+).
LEVEL_THRESHOLDS: tuple[tuple[int, float], ...] = (
    (5, 90.0),
    (4, 75.0),
    (3, 60.0),
    (2, 40.0),
)
LEVEL_NAMES: dict[int, str] = {5: "重大", 4: "显著", 3: "关注", 2: "一般", 1: "轻微"}


def event_type(root_code: str) -> str:
    """Return the Chinese event category for a CAMEO root code."""
    return ROOT_TYPES.get(root_code, "其他")


def type_severity(root_code: str) -> float:
    """Return the 0-30 severity contribution of the CAMEO root code."""
    return ROOT_SEVERITY.get(root_code, 3.0)


def scale_score(goldstein: float | None) -> float:
    """Return the 0-20 contribution of GDELT's Goldstein scale."""
    if goldstein is None:
        return 0.0
    return min(abs(goldstein) / 10.0, 1.0) * 20.0


def spread_score(num_sources: int) -> float:
    """Return the 0-25 contribution of independent source coverage."""
    if num_sources <= 0:
        return 0.0
    return min(math.log1p(num_sources) / math.log1p(50), 1.0) * 25.0


def tone_score(avg_tone: float | None) -> float:
    """Return the 0-10 contribution of a negative average article tone."""
    if avg_tone is None or avg_tone >= 0:
        return 0.0
    return min(-avg_tone / 10.0, 1.0) * 10.0


def region_score(region_weight: float) -> float:
    """Return the 0-15 contribution of the static region weight."""
    return max(0.0, min(region_weight, 15.0))


def intensity_score(
    *,
    root_code: str,
    goldstein: float | None,
    num_sources: int,
    avg_tone: float | None,
    region_weight: float,
) -> tuple[float, float, float, float, float, float]:
    """Return the five components followed by the rounded total (0-100)."""
    severity = type_severity(root_code)
    scale = scale_score(goldstein)
    spread = spread_score(num_sources)
    tone = tone_score(avg_tone)
    region = region_score(region_weight)
    total = round(severity + scale + spread + tone + region, 1)
    return severity, scale, spread, tone, region, total


def level_for(intensity: float) -> int:
    """Map a 0-100 intensity score to a 1-5 display level."""
    for level, threshold in LEVEL_THRESHOLDS:
        if intensity >= threshold:
            return level
    return 1


def level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, str(level))
