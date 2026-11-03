"""Read models for the local Web UI API.

The API deliberately reuses the CLI's modules (``Database``, indicators, event
alignment) instead of shelling out to the command line.  Only the shapes are
new: price series with indicators, aggregated overlay data, paginated event
rows and collection-run status.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Sequence

from ..db import Database
from ..events.alignment import align_events, align_market_date, trading_days
from ..events.severity import LEVEL_NAMES, TYPE_COLORS
from ..events.watchlist import ALL_REGIONS
from ..indicators import bars_to_frame, compute_indicators, latest_snapshot
from ..instruments import DEFAULT_SYMBOLS, INSTRUMENTS, get_instrument
from .jobs import refresh_run

DEFAULT_PRICE_DAYS = 180
DEFAULT_EVENT_DAYS = 30
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_POINTS = 1500


def today() -> date:
    return datetime.now(tz=timezone.utc).date()


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _slice_frame(frame, *, days: int, start: Optional[date]):
    """Return the requested window, keeping indicators computed on full history."""
    if start is not None:
        selected = frame[frame["date"] >= start]
    elif days and days > 0:
        selected = frame.tail(days)
    else:
        selected = frame
    return selected.reset_index(drop=True)


def _downsample_indices(total: int, max_points: int) -> Optional[List[int]]:
    if not max_points or total <= max_points:
        return None
    step = total / float(max_points)
    indices = {0, total - 1}
    for index in range(max_points):
        indices.add(min(total - 1, int(round(index * step))))
    return sorted(indices)


def instrument_cards(db: Database) -> List[dict]:
    """Latest snapshot for every registered instrument, including empty ones."""
    cards = []
    for symbol, instrument in INSTRUMENTS.items():
        bars = db.query(symbol)
        latest_date = None
        latest_close = None
        daily_change = None
        trend = "数据不足"
        if bars:
            frame = compute_indicators(bars_to_frame(bars))
            snapshot = latest_snapshot(frame)
            if snapshot is not None:
                latest_date = _as_date(snapshot.date)
                latest_close = _num(snapshot.close)
                daily_change = _num(snapshot.daily_change_pct)
                trend = snapshot.trend
        days_since = (today() - latest_date).days if latest_date else None
        cards.append(
            {
                "symbol": symbol,
                "name": instrument.name,
                "unit": instrument.unit,
                "currency": instrument.currency,
                "sources": list(instrument.sources),
                "latest_date": latest_date.isoformat() if latest_date else None,
                "count": len(bars),
                "latest_close": latest_close,
                "daily_change_pct": daily_change,
                "trend": trend,
                "days_since": days_since,
                "stale": bool(days_since is not None and days_since > 4),
            }
        )
    return cards


def _price_records(frame) -> List[dict]:
    records = []
    for _, row in frame.iterrows():
        records.append(
            {
                "date": _as_date(row["date"]).isoformat(),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "close": _num(row.get("close")),
                "volume": _num(row.get("volume")),
                "ma5": _num(row.get("ma5")),
                "ma20": _num(row.get("ma20")),
                "ma60": _num(row.get("ma60")),
                "daily_change_pct": _num(row.get("daily_change_pct")),
                "volatility_pct": _num(row.get("volatility_pct")),
                "trend": str(row.get("trend") or "数据不足"),
            }
        )
    return records


def prices_payload(
    db: Database,
    symbol: str,
    *,
    days: int = DEFAULT_PRICE_DAYS,
    start: Optional[date] = None,
    end: Optional[date] = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict:
    instrument = get_instrument(symbol)
    bars = db.query(symbol, end=end)
    payload = {
        "symbol": symbol,
        "name": instrument.name,
        "unit": instrument.unit,
        "currency": instrument.currency,
        "bars": [],
        "count": 0,
        "total_count": 0,
        "downsampled": False,
        "start": None,
        "end": None,
    }
    if not bars:
        return payload

    frame = compute_indicators(bars_to_frame(bars))
    payload["total_count"] = len(frame)
    window = _slice_frame(frame, days=days, start=start)
    if window.empty:
        return payload

    payload["start"] = _as_date(window["date"].iloc[0]).isoformat()
    payload["end"] = _as_date(window["date"].iloc[-1]).isoformat()
    payload["count"] = len(window)
    indices = _downsample_indices(len(window), max_points)
    if indices is not None:
        window = window.iloc[indices].reset_index(drop=True)
        payload["downsampled"] = True
        payload["count"] = len(window)
    payload["bars"] = _price_records(window)
    return payload


def _market_dates(db: Database, symbol: str, rows: Sequence[dict]) -> dict:
    """Map event ids to the next trading day with a close."""
    bars = db.query(symbol)
    if not bars:
        return {}
    days = trading_days([bar.date for bar in bars])
    market = {}
    for row in rows:
        occurred = date.fromisoformat(str(row["occurred_at"])[:10])
        market_date = align_market_date(occurred, days)
        if market_date is not None:
            market.setdefault(row["source_event_id"], market_date)
    return market


def events_payload(
    db: Database,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    days: int = DEFAULT_EVENT_DAYS,
    regions: Optional[Sequence[str]] = None,
    min_level: int = 1,
    event_type: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    symbol: str = "XAU",
) -> dict:
    end_date = end or today()
    start_date = start or (end_date - timedelta(days=max(1, days) - 1))
    if start_date > end_date:
        raise ValueError("起始日期不能晚于结束日期")
    selected_regions = [region for region in (regions or []) if region]
    total, rows = db.query_events_page(
        start=start_date,
        end=end_date,
        regions=selected_regions or None,
        min_level=min_level,
        event_type=event_type or None,
        keyword=keyword or None,
        limit=limit,
        offset=offset,
    )
    market = _market_dates(db, symbol, rows)
    events = []
    for row in rows:
        market_date = market.get(row["source_event_id"])
        published = row.get("published_at")
        events.append(
            {
                "source": row["source"],
                "source_event_id": row["source_event_id"],
                "occurred_at": str(row["occurred_at"])[:10],
                "published_at": str(published)[:10] if published else None,
                "market_date": market_date.isoformat() if market_date else None,
                "country": row["country"],
                "region": row["region"],
                "event_type": row["event_type"],
                "level": row["level"],
                "level_name": LEVEL_NAMES.get(row["level"], str(row["level"])),
                "intensity": _num(row["intensity"]),
                "num_sources": row["num_sources"],
                "summary": row["summary"] or "",
                "url": row["url"] or "",
            }
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "events": events,
    }


def _event_point(event, market_date: date, price: Optional[float]) -> dict:
    return {
        "source_event_id": event.source_event_id,
        "occurred_at": event.occurred_at.isoformat(),
        "market_date": market_date.isoformat(),
        "region": event.region,
        "country": event.country,
        "event_type": event.event_type,
        "level": event.level,
        "level_name": LEVEL_NAMES.get(event.level, str(event.level)),
        "intensity": _num(event.intensity),
        "num_sources": event.num_sources,
        "summary": event.summary,
        "url": event.url,
        "price": _num(price) if price is not None else None,
    }


def overlay_payload(
    db: Database,
    symbol: str = "XAU",
    *,
    days: int = DEFAULT_PRICE_DAYS,
    start: Optional[date] = None,
    end: Optional[date] = None,
    min_level: int = 1,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict:
    instrument = get_instrument(symbol)
    bars = db.query(symbol, end=end)
    payload = {
        "symbol": symbol,
        "name": instrument.name,
        "unit": instrument.unit,
        "currency": instrument.currency,
        "price": [],
        "events": [],
        "timeline": [],
        "top": [],
        "regions": [],
        "event_count": 0,
        "timeline_count": 0,
        "downsampled": False,
        "start": None,
        "end": None,
    }
    if not bars:
        return payload

    frame = compute_indicators(bars_to_frame(bars))
    all_days = trading_days(frame["date"])
    window = _slice_frame(frame, days=days, start=start)
    if window.empty:
        return payload

    window_start = _as_date(window["date"].iloc[0])
    window_end = _as_date(window["date"].iloc[-1])
    payload["start"] = window_start.isoformat()
    payload["end"] = window_end.isoformat()

    price_by_day = {
        _as_date(row["date"]): _num(row["close"]) for _, row in window.iterrows()
    }
    price_frame = window
    indices = _downsample_indices(len(price_frame), max_points)
    if indices is not None:
        price_frame = price_frame.iloc[indices].reset_index(drop=True)
        payload["downsampled"] = True
    payload["price"] = [
        {
            "date": _as_date(row["date"]).isoformat(),
            "close": _num(row.get("close")),
            "ma20": _num(row.get("ma20")),
        }
        for _, row in price_frame.iterrows()
    ]

    # Events before the window can still roll forward into its first session.
    events = db.query_events_lean(
        start=window_start - timedelta(days=14),
        end=window_end,
        min_level=min_level,
    )
    aligned = [
        (event, market_date)
        for event, market_date in align_events(events, all_days)
        if window_start <= market_date <= window_end
    ]
    payload["event_count"] = len(aligned)

    by_day = {}
    timeline = {}
    for event, market_date in aligned:
        current = by_day.get(market_date)
        if current is None or event.intensity > current.intensity:
            by_day[market_date] = event
        key = (event.region, market_date)
        current = timeline.get(key)
        if current is None or event.intensity > current.intensity:
            timeline[key] = event

    payload["events"] = [
        _event_point(by_day[day], day, price_by_day.get(day))
        for day in sorted(by_day)
    ]
    payload["timeline"] = []
    for (region, market_date), event in sorted(
        timeline.items(), key=lambda item: (item[0][1], ALL_REGIONS.index(item[0][0]))
    ):
        point = _event_point(event, market_date, price_by_day.get(market_date))
        point["region"] = region
        payload["timeline"].append(point)
    payload["timeline_count"] = len(payload["timeline"])
    payload["regions"] = [
        region
        for region in ALL_REGIONS
        if any(item["region"] == region for item in payload["timeline"])
    ]

    top = sorted(by_day.values(), key=lambda event: event.intensity, reverse=True)[:5]
    top_dates = {
        event.source_event_id: day
        for day, event in by_day.items()
        if event in top
    }
    payload["top"] = [
        _event_point(event, top_dates[event.source_event_id], price_by_day.get(top_dates[event.source_event_id]))
        for event in top
    ]
    return payload


def _timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_payload(db: Database, run: Optional[dict]) -> Optional[dict]:
    run = refresh_run(db, run)
    if run is None:
        return None
    try:
        params = json.loads(run.get("params") or "{}")
    except (TypeError, ValueError):
        params = {}
    try:
        summary = json.loads(run["summary"]) if run.get("summary") else None
    except (TypeError, ValueError):
        summary = None
    started = _timestamp(run.get("started_at"))
    finished = _timestamp(run.get("finished_at"))
    duration = (
        int((finished - started).total_seconds())
        if started and finished
        else None
    )
    return {
        "id": run["id"],
        "kind": run["kind"],
        "source": run["source"],
        "status": run["status"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "progress_current": run.get("progress_current", 0),
        "progress_total": run.get("progress_total", 0),
        "message": run.get("message"),
        "summary": summary,
        "error": run.get("error"),
        "params": params,
        "duration_seconds": duration,
    }


def runs_payload(db: Database, limit: int = 20) -> List[dict]:
    return [
        payload
        for payload in (
            run_payload(db, run) for run in db.list_runs(limit=limit)
        )
        if payload is not None
    ]


def _run_brief(run: Optional[dict]) -> Optional[dict]:
    if run is None:
        return None
    return {
        "id": run["id"],
        "kind": run["kind"],
        "status": run["status"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "progress_current": run.get("progress_current", 0),
        "progress_total": run.get("progress_total", 0),
        "message": run.get("message"),
        "error": run.get("error"),
    }


def _last_run(runs: Sequence[dict], kind: str, statuses: Sequence[str]):
    for run in runs:
        if run["kind"] == kind and run["status"] in statuses:
            return _run_brief(run)
    return None


def _brief_payload(run: dict) -> dict:
    return {
        "id": run["id"],
        "kind": run["kind"],
        "status": run["status"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "progress_current": run.get("progress_current", 0),
        "progress_total": run.get("progress_total", 0),
        "message": run.get("message"),
        "error": run.get("error"),
    }


def _instrument_runs(runs: Sequence[dict], symbol: str):
    """Return (last_success, last_failure) for one instrument.

    A multi-symbol run counts as a per-instrument success only when that
    symbol produced a source, so an empty AU9999 card does not inherit XAU's
    successful run.
    """
    last_success = None
    last_failure = None
    for run in runs:
        if run["kind"] != "prices":
            continue
        params = run.get("params") or {}
        if symbol not in (params.get("symbols") or []):
            continue
        results = (run.get("summary") or {}).get("results") or []
        result = next(
            (item for item in results if item.get("symbol") == symbol), None
        )
        failed = run["status"] in ("failed", "interrupted") or (
            result is not None and result.get("source") is None
        )
        if failed and last_failure is None:
            last_failure = _brief_payload(run)
        if not failed and last_success is None:
            last_success = _brief_payload(run)
        if last_success is not None and last_failure is not None:
            break
    return last_success, last_failure


def status_payload(db: Database) -> dict:
    cards = instrument_cards(db)
    first_date, latest_date = db.event_date_range()
    latest_published = db.latest_event_published_at()
    raw_runs = [refresh_run(db, run) for run in db.list_runs(limit=50)]
    raw_runs = [run for run in raw_runs if run is not None]
    run_items = [
        payload
        for payload in (run_payload(db, run) for run in raw_runs)
        if payload is not None
    ]
    for card in cards:
        success, failure = _instrument_runs(run_items, card["symbol"])
        card["last_success"] = success
        card["last_failure"] = failure
    active = next(
        (run for run in raw_runs if run["status"] in ("queued", "running")), None
    )
    success = ("succeeded", "partial")
    failure = ("failed", "interrupted")
    return {
        "today": today().isoformat(),
        "instruments": cards,
        "events": {
            "count": db.count_events(),
            "first_date": first_date.isoformat() if first_date else None,
            "latest_date": latest_date.isoformat() if latest_date else None,
            "latest_published_at": (
                latest_published.isoformat() if latest_published else None
            ),
        },
        "last_runs": {
            "prices_success": _last_run(raw_runs, "prices", success),
            "prices_failure": _last_run(raw_runs, "prices", failure),
            "events_success": _last_run(raw_runs, "events", success),
            "events_failure": _last_run(raw_runs, "events", failure),
        },
        "active_run": run_payload(db, active) if active else None,
        "runs": run_items[:12],
    }


def meta_payload() -> dict:
    return {
        "today": today().isoformat(),
        "instruments": [
            {
                "symbol": symbol,
                "name": instrument.name,
                "unit": instrument.unit,
                "currency": instrument.currency,
                "sources": list(instrument.sources),
            }
            for symbol, instrument in INSTRUMENTS.items()
        ],
        "default_symbols": list(DEFAULT_SYMBOLS),
        "event_regions": list(ALL_REGIONS),
        "event_types": [
            {"name": name, "color": color} for name, color in TYPE_COLORS.items()
        ],
        "levels": [
            {"value": level, "name": LEVEL_NAMES[level]}
            for level in (5, 4, 3, 2, 1)
        ],
        "defaults": {
            "price_days": DEFAULT_PRICE_DAYS,
            "event_days": DEFAULT_EVENT_DAYS,
            "page_size": DEFAULT_PAGE_SIZE,
            "max_points": DEFAULT_MAX_POINTS,
        },
    }
