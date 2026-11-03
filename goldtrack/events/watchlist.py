"""Watchlist regions and country metadata.

GDELT's ``ActionGeo_CountryCode`` uses the two-letter FIPS 10-4 code, which is
different from ISO 3166-1 alpha-2 (for example ``RS`` is Russia and ``BO`` is
Belarus).  The mapping below is deliberately explicit so event regions never
depend on an external library at runtime.
"""

from __future__ import annotations

# Chinese display names for every country on the watchlist.  Unknown locations
# fall back to the first component of GDELT's full location name.
FIPS_NAMES: dict[str, str] = {
    "IR": "伊朗",
    "IS": "以色列",
    "WE": "约旦河西岸",
    "GZ": "加沙",
    "LE": "黎巴嫩",
    "SY": "叙利亚",
    "IZ": "伊拉克",
    "QA": "卡塔尔",
    "KU": "科威特",
    "BA": "巴林",
    "JO": "约旦",
    "TU": "土耳其",
    "SA": "沙特阿拉伯",
    "AE": "阿联酋",
    "EG": "埃及",
    "YM": "也门",
    "SU": "苏丹",
    "OD": "南苏丹",
    "ER": "厄立特里亚",
    "DJ": "吉布提",
    "SO": "索马里",
    "RS": "俄罗斯",
    "UP": "乌克兰",
    "BO": "白俄罗斯",
    "TW": "台湾",
    "CH": "中国",
    "KN": "朝鲜",
    "KS": "韩国",
    "AF": "阿富汗",
    "PK": "巴基斯坦",
    "IN": "印度",
    "BM": "缅甸",
    "TH": "泰国",
    "CB": "柬埔寨",
    "VM": "越南",
    "RP": "菲律宾",
    "ID": "印度尼西亚",
    "MY": "马来西亚",
    "SN": "新加坡",
    # Common non-watchlist countries that can appear as globally significant
    # conflict locations.  Unknown FIPS codes fall back to the code itself.
    "US": "美国",
    "UK": "英国",
    "FR": "法国",
    "GM": "德国",
    "CA": "加拿大",
    "AU": "澳大利亚",
    "NZ": "新西兰",
    "BR": "巴西",
    "AR": "阿根廷",
    "SF": "南非",
    "IT": "意大利",
    "SP": "西班牙",
    "PO": "葡萄牙",
    "NL": "荷兰",
    "BE": "比利时",
    "SW": "瑞典",
    "FI": "芬兰",
    "NO": "挪威",
    "DA": "丹麦",
    "PL": "波兰",
    "RO": "罗马尼亚",
    "HU": "匈牙利",
    "GR": "希腊",
    "MX": "墨西哥",
    "VE": "委内瑞拉",
    "CO": "哥伦比亚",
    "HA": "海地",
    "NI": "尼日利亚",
    "NG": "尼日尔",
    "ML": "马里",
    "CD": "乍得",
    "KE": "肯尼亚",
    "ET": "埃塞俄比亚",
    "UG": "乌干达",
    "TZ": "坦桑尼亚",
    "MO": "摩洛哥",
    "TS": "突尼斯",
    "LY": "利比亚",
    "AG": "阿尔及利亚",
}

# Regions are ordered from highest to lowest market relevance.  An event is
# assigned to exactly one region using its action location, so a country must
# only appear once.
REGIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "中东",
        (
            "IR",
            "IS",
            "WE",
            "GZ",
            "LE",
            "SY",
            "IZ",
            "QA",
            "KU",
            "BA",
            "JO",
            "TU",
            "SA",
            "AE",
            "EG",
        ),
    ),
    ("俄乌", ("RS", "UP", "BO")),
    ("台海", ("TW", "CH")),
    ("红海/非洲之角", ("YM", "SU", "OD", "ER", "DJ", "SO")),
    ("朝鲜半岛", ("KN", "KS")),
    ("南亚", ("AF", "PK", "IN")),
    ("东南亚", ("BM", "TH", "CB", "VM", "RP", "ID", "MY", "SN")),
)

# Static relevance weight (0-15) used as one component of the event intensity.
# Energy corridors, major-power flashpoints and active conflicts rank highest.
REGION_WEIGHTS: dict[str, float] = {
    "中东": 15.0,
    "俄乌": 15.0,
    "台海": 14.0,
    "红海/非洲之角": 12.0,
    "朝鲜半岛": 12.0,
    "南亚": 10.0,
    "东南亚": 8.0,
    "其他": 5.0,
}

COUNTRY_REGION: dict[str, str] = {
    code: region for region, codes in REGIONS for code in codes
}

WATCHLIST_REGIONS: tuple[str, ...] = tuple(region for region, _ in REGIONS)
ALL_REGIONS: tuple[str, ...] = WATCHLIST_REGIONS + ("其他",)


def country_name(code: str, fullname: str = "") -> str:
    """Return a Chinese country label, falling back to the FIPS code.

    ``fullname`` is only used when GDELT provides no country code; its last
    component is the country level name in GDELT's ``City, Region, Country``
    convention.
    """
    if code:
        return FIPS_NAMES.get(code, code)
    if fullname:
        return fullname.split(",")[-1].strip()
    return "未知"


def country_region(code: str) -> str:
    """Return the watchlist region for a FIPS country code, or ``其他``."""
    return COUNTRY_REGION.get(code, "其他")


def is_watchlisted(code: str) -> bool:
    return code in COUNTRY_REGION
