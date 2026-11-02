"""Download and parse GDELT event exports.

Two GDELT layouts are supported because GDELT publishes the same event stream
in a daily archive (58 columns) and in 15-minute files (61 columns):

* daily: ``https://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip``
* 15-minute: ``https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.export.CSV.zip``

The parser is therefore length-driven.  Phase one uses the daily archive,
which is one request per day and matches the daily gold price granularity.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from dataclasses import asdict
from datetime import date
from typing import Iterable

import requests

from ..sources.http import USER_AGENT
from .models import GdeltRow, GeoEvent
from .severity import (
    SIGNIFICANT_ROOTS,
    event_type,
    intensity_score,
    level_for,
)
from .watchlist import REGION_WEIGHTS, country_name, country_region

logger = logging.getLogger(__name__)

DAILY_URL = "https://data.gdeltproject.org/events/{stamp}.export.CSV.zip"
SLOT_URL = "https://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"

GLOBAL_MAJOR_ROOTS = frozenset({"19", "20"})
DEFAULT_TIMEOUT = 90.0

# Column indexes shared by both layouts (0-based).
_IDX_EVENT_ID = 0
_IDX_DAY = 1
_IDX_ACTOR1_NAME = 6
_IDX_ACTOR1_COUNTRY = 7
_IDX_ACTOR2_NAME = 16
_IDX_ACTOR2_COUNTRY = 17
_IDX_IS_ROOT = 25
_IDX_EVENT_CODE = 26
_IDX_EVENT_BASE = 27
_IDX_ROOT = 28
_IDX_QUAD = 29
_IDX_GOLDSTEIN = 30
_IDX_MENTIONS = 31
_IDX_SOURCES = 32
_IDX_ARTICLES = 33
_IDX_TONE = 34


class GdeltError(RuntimeError):
    """Raised when a GDELT export cannot be downloaded or parsed."""


def daily_url(day: date) -> str:
    return DAILY_URL.format(stamp=day.strftime("%Y%m%d"))


def slot_url(stamp: str) -> str:
    """Return the URL of one 15-minute export, e.g. ``20261102120000``."""
    return SLOT_URL.format(stamp=stamp)


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_day(value: str) -> date | None:
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except (TypeError, ValueError):
        return None


def _layout(column_count: int) -> tuple[int, int, int, int] | None:
    """Return ``(action_geo, lat, lon, url)`` indexes for a row layout."""
    if column_count == 58:  # GDELT daily archive (1.0 layout)
        return 49, 53, 54, 57
    if column_count == 61:  # GDELT 2.0 15-minute layout
        return 51, 56, 57, 60
    return None


def parse_export(text: str) -> list[GdeltRow]:
    """Parse a GDELT TSV export, skipping malformed or incomplete rows."""
    rows: list[GdeltRow] = []
    for fields in csv.reader(io.StringIO(text), delimiter="\t"):
        layout = _layout(len(fields))
        if layout is None:
            continue
        geo, lat_index, lon_index, url_index = layout
        event_id = fields[_IDX_EVENT_ID].strip()
        day = _to_day(fields[_IDX_DAY].strip())
        if not event_id or day is None:
            continue
        rows.append(
            GdeltRow(
                event_id=event_id,
                day=day,
                is_root_event=fields[_IDX_IS_ROOT].strip() == "1",
                event_code=fields[_IDX_EVENT_CODE].strip(),
                event_base_code=fields[_IDX_EVENT_BASE].strip(),
                root_code=fields[_IDX_ROOT].strip(),
                quad_class=_to_int(fields[_IDX_QUAD]),
                goldstein=_to_float(fields[_IDX_GOLDSTEIN]),
                num_mentions=_to_int(fields[_IDX_MENTIONS]),
                num_sources=_to_int(fields[_IDX_SOURCES]),
                num_articles=_to_int(fields[_IDX_ARTICLES]),
                avg_tone=_to_float(fields[_IDX_TONE]),
                actor1_name=fields[_IDX_ACTOR1_NAME].strip(),
                actor1_country=fields[_IDX_ACTOR1_COUNTRY].strip(),
                actor2_name=fields[_IDX_ACTOR2_NAME].strip(),
                actor2_country=fields[_IDX_ACTOR2_COUNTRY].strip(),
                action_country=fields[geo + 2].strip(),
                action_fullname=fields[geo + 1].strip(),
                lat=_to_float(fields[lat_index]),
                lon=_to_float(fields[lon_index]),
                url=fields[url_index].strip(),
            )
        )
    return rows


def fetch_export(
    day: date,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> list[GdeltRow]:
    """Download and parse one GDELT daily export file."""
    url = daily_url(day)
    client = session or requests
    try:
        response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        raise GdeltError(f"下载 {url} 失败: {exc}") from exc
    if response.status_code == 404:
        raise GdeltError(f"GDELT 日文件不存在: {url}")
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GdeltError(f"下载 {url} 失败: {exc}") from exc
    return parse_export_bytes(response.content, url=url)


def parse_export_bytes(content: bytes, *, url: str = "") -> list[GdeltRow]:
    """Unzip and parse a downloaded GDELT export archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise GdeltError(f"GDELT 文件不是合法 ZIP: {url or exc}") from exc
    rows: list[GdeltRow] = []
    for name in archive.namelist():
        if not name.endswith(".CSV"):
            continue
        text = archive.read(name).decode("utf-8", errors="replace")
        rows.extend(parse_export(text))
    if not rows:
        raise GdeltError(f"GDELT 文件没有可解析的事件: {url}")
    return rows


def _summary(row: GdeltRow, country: str, category: str) -> str:
    actors = ""
    if row.actor1_name or row.actor2_name:
        actors = f"（{row.actor1_name or '?'} → {row.actor2_name or '?'}）"
    return f"{country}：{category}{actors}"


def _event_from_row(
    row: GdeltRow,
    published_at: date,
    *,
    region: str,
    region_weight: float,
) -> GeoEvent:
    country = country_name(row.action_country, row.action_fullname)
    category = event_type(row.root_code)
    severity, scale, spread, tone, region_component, total = intensity_score(
        root_code=row.root_code,
        goldstein=row.goldstein,
        num_sources=row.num_sources,
        avg_tone=row.avg_tone,
        region_weight=region_weight,
    )
    return GeoEvent(
        source="gdelt",
        source_event_id=row.event_id,
        occurred_at=row.day,
        published_at=published_at,
        country_code=row.action_country,
        country=country,
        region=region,
        lat=row.lat,
        lon=row.lon,
        event_code=row.event_code,
        event_base_code=row.event_base_code,
        root_code=row.root_code,
        quad_class=row.quad_class,
        event_type=category,
        goldstein=row.goldstein,
        num_mentions=row.num_mentions,
        num_sources=row.num_sources,
        num_articles=row.num_articles,
        avg_tone=row.avg_tone,
        type_severity=severity,
        scale_score=scale,
        spread_score=spread,
        tone_score=tone,
        region_weight=region_component,
        intensity=total,
        level=level_for(total),
        summary=_summary(row, country, category),
        url=row.url,
        raw_json=json.dumps(asdict(row), ensure_ascii=False, default=str),
    )


def build_events(
    rows: Iterable[GdeltRow],
    published_at: date,
    *,
    min_sources: int = 3,
    global_min_sources: int = 10,
    all_countries: bool = False,
    min_intensity: float = 0.0,
) -> list[GeoEvent]:
    """Filter watchlist-relevant rows and attach rule-based intensity scores.

    An event is kept when it is a root event with a coercive/violent CAMEO root
    code, has at least ``min_sources`` independent sources, and either happens
    in a watchlist region or is a globally significant fight/mass-violence
    event with at least ``global_min_sources`` sources.
    """
    events: list[GeoEvent] = []
    for row in rows:
        if not row.is_root_event:
            continue
        if row.root_code not in SIGNIFICANT_ROOTS:
            continue
        if row.num_sources < min_sources:
            continue
        region = country_region(row.action_country)
        globally_major = (
            row.root_code in GLOBAL_MAJOR_ROOTS
            and row.num_sources >= global_min_sources
        )
        if not all_countries and region == "其他" and not globally_major:
            continue
        event = _event_from_row(
            row,
            published_at,
            region=region,
            region_weight=REGION_WEIGHTS.get(region, 5.0),
        )
        if event.intensity < min_intensity:
            continue
        events.append(event)
    return events
