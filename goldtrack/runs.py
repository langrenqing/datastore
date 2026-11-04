"""Recorded collection runs shared by the CLI and the local Web UI.

A run row is created *before* the collector starts, which gives the Web UI a
stable handle for progress, failures and history.  The scheduler and the Web
UI both go through this module so ``/api/status`` can report the most recent
successful and failed collection for each target.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Sequence

from .db import Database
from .events.fetch import collect_events
from .events.models import EventFetchSummary
from .fetch import collect_all
from .models import FetchResult
from .sources.base import RetryPolicy


def price_params(
    symbols: Sequence[str],
    *,
    full: bool = False,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Serialise the arguments of a prices run for the ``params`` column."""
    return {
        "symbols": list(symbols),
        "full": bool(full),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def event_params(
    *,
    start: date | None = None,
    end: date | None = None,
    days: int = 30,
    min_sources: int = 3,
    global_min_sources: int = 10,
    all_countries: bool = False,
    min_intensity: float = 0.0,
) -> dict:
    """Serialise the arguments of an events run for the ``params`` column."""
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "days": days,
        "min_sources": min_sources,
        "global_min_sources": global_min_sources,
        "all_countries": bool(all_countries),
        "min_intensity": min_intensity,
    }


def _price_status(results: Sequence[FetchResult]) -> str:
    if not results:
        return "failed"
    ok = [result for result in results if result.source is not None]
    if len(ok) == len(results):
        return "succeeded"
    if ok:
        return "partial"
    return "failed"


def _price_summary(results: Sequence[FetchResult]) -> dict:
    return {
        "results": [
            {
                "symbol": result.symbol,
                "source": result.source,
                "stored": result.stored,
                "skipped": result.skipped,
                "latest": result.bars[-1].date.isoformat() if result.bars else None,
                "errors": list(result.errors),
            }
            for result in results
        ]
    }


def _event_status(summary: EventFetchSummary) -> str:
    if not summary.failures:
        return "succeeded"
    if summary.stored > 0:
        return "partial"
    return "failed"


def _event_summary(summary: EventFetchSummary) -> dict:
    failures = summary.failures
    return {
        "start": summary.start.isoformat() if summary.start else None,
        "end": summary.end.isoformat() if summary.end else None,
        "days": summary.days,
        "parsed": summary.parsed,
        "relevant": summary.relevant,
        "stored": summary.stored,
        "failure_count": len(failures),
        "failures": [
            {"day": failure.day.isoformat(), "error": failure.error}
            for failure in failures[:20]
        ],
    }


def run_prices(
    db: Database,
    run_id: int,
    *,
    symbols: Sequence[str],
    full: bool = False,
    start: date | None = None,
    end: date | None = None,
    retry: RetryPolicy | None = None,
    pid: int | None = None,
) -> list[FetchResult]:
    """Run one recorded prices collection and update its run row."""
    db.start_run(run_id, pid=os.getpid() if pid is None else pid)

    def on_symbol(done: int, total: int, result: FetchResult) -> None:
        if result.source:
            message = f"{result.symbol} 完成，写入 {result.stored} 条"
        else:
            message = f"{result.symbol} 失败"
        db.set_run_progress(run_id, done, total, message)

    try:
        results = collect_all(
            db,
            symbols,
            full=full,
            start=start,
            end=end,
            retry=retry,
            on_symbol=on_symbol,
        )
        db.finish_run(run_id, _price_status(results), summary=_price_summary(results))
        return results
    except Exception as exc:
        db.finish_run(run_id, "failed", error=str(exc))
        raise


def run_events(
    db: Database,
    run_id: int,
    *,
    start: date | None = None,
    end: date | None = None,
    days: int = 30,
    min_sources: int = 3,
    global_min_sources: int = 10,
    all_countries: bool = False,
    min_intensity: float = 0.0,
    retry: RetryPolicy | None = None,
    pid: int | None = None,
) -> EventFetchSummary:
    """Run one recorded GDELT collection and update its run row."""
    db.start_run(run_id, pid=os.getpid() if pid is None else pid)

    def on_day(done: int, total: int, result) -> None:
        if result.error:
            message = f"{result.day} 失败"
        else:
            message = f"{result.day} 解析 {result.parsed:,} / 入库 {result.stored:,}"
        db.set_run_progress(run_id, done, total, message)

    try:
        summary = collect_events(
            db,
            start=start,
            end=end,
            days=days,
            min_sources=min_sources,
            global_min_sources=global_min_sources,
            all_countries=all_countries,
            min_intensity=min_intensity,
            retry=retry,
            on_day=on_day,
        )
        db.finish_run(run_id, _event_status(summary), summary=_event_summary(summary))
        return summary
    except Exception as exc:
        db.finish_run(run_id, "failed", error=str(exc))
        raise
