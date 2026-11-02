"""Incremental GDELT collection orchestration."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from ..db import Database
from ..sources.base import RetryPolicy, SourceError, call_with_retry
from . import gdelt
from .gdelt import GdeltError
from .models import DayFetchResult, EventFetchSummary, GdeltRow

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 30
OVERLAP_DAYS = 1

RowFetcher = Callable[[date], list[GdeltRow]]


def _yesterday_utc() -> date:
    return datetime.now(tz=timezone.utc).date() - timedelta(days=1)


def resolve_range(
    db: Database,
    *,
    start: date | None,
    end: date | None,
    days: int,
) -> tuple[date, date]:
    """Return the inclusive date range to collect, clipped to complete days."""
    last_complete = _yesterday_utc()
    effective_end = end or last_complete
    if effective_end > last_complete:
        logger.warning(
            "GDELT 日文件通常在次日提供，结束日期截止到 %s", last_complete.isoformat()
        )
        effective_end = last_complete
    if start is None:
        latest = db.latest_event_published_at()
        if latest is not None:
            start = latest - timedelta(days=OVERLAP_DAYS)
        else:
            start = effective_end - timedelta(days=max(days, 1) - 1)
    if start > effective_end:
        return start, effective_end
    return start, effective_end


def collect_events(
    db: Database,
    *,
    start: date | None = None,
    end: date | None = None,
    days: int = DEFAULT_BACKFILL_DAYS,
    min_sources: int = 3,
    global_min_sources: int = 10,
    all_countries: bool = False,
    min_intensity: float = 0.0,
    retry: RetryPolicy | None = None,
    fetch_day: RowFetcher | None = None,
    sleep: Callable[[float], None] | None = None,
) -> EventFetchSummary:
    """Fetch daily GDELT files from ``start`` to ``end`` and upsert events.

    A failed day never aborts the remaining days; the summary records it.
    Without an explicit range the collector resumes one day before the newest
    stored source date, so late GDELT corrections are picked up idempotently.
    """
    policy = retry or RetryPolicy()
    fetcher = fetch_day or gdelt.fetch_export
    first, last = resolve_range(db, start=start, end=end, days=days)
    summary = EventFetchSummary(start=first, end=last)
    if first > last:
        return summary

    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        try:
            if sleep is None:
                rows = call_with_retry(lambda day=day: fetcher(day), policy)
            else:
                rows = call_with_retry(
                    lambda day=day: fetcher(day), policy, sleep=sleep
                )
        except (SourceError, GdeltError) as exc:
            message = f"{day.isoformat()} 获取失败: {exc}"
            logger.warning(message)
            summary.results.append(DayFetchResult(day=day, parsed=0, error=message))
            continue

        events = gdelt.build_events(
            rows,
            published_at=day,
            min_sources=min_sources,
            global_min_sources=global_min_sources,
            all_countries=all_countries,
            min_intensity=min_intensity,
        )
        stored = db.upsert_events(events)
        logger.info(
            "%s parsed=%s relevant=%s stored=%s", day, len(rows), len(events), stored
        )
        summary.results.append(
            DayFetchResult(
                day=day, parsed=len(rows), relevant=len(events), stored=stored
            )
        )
    return summary
