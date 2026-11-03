"""Collection orchestration: multi-source fetch, retry and incremental storage."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Callable, Sequence

from .db import Database
from .instruments import get_instrument
from .models import Bar, FetchResult, Instrument
from .sources import resolve
from .sources.base import RetryPolicy, SourceError, call_with_retry

logger = logging.getLogger(__name__)

DEFAULT_OVERLAP_DAYS = 5


def _increment_start(db: Database, instrument: Instrument, overlap_days: int) -> date | None:
    latest = db.latest_date(instrument.symbol)
    if latest is None:
        return None
    return latest - timedelta(days=overlap_days)


def _filter_bars(
    bars: Sequence[Bar],
    start: date | None,
    end: date | None,
) -> list[Bar]:
    selected = []
    for bar in bars:
        if start is not None and bar.date < start:
            continue
        if end is not None and bar.date > end:
            continue
        selected.append(bar)
    return selected


def collect_instrument(
    db: Database,
    instrument: Instrument,
    *,
    full: bool = False,
    start: date | None = None,
    end: date | None = None,
    retry: RetryPolicy | None = None,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> FetchResult:
    """Fetch one instrument from its sources in order and upsert valid bars.

    Each source is retried according to ``retry``; when it still fails the next
    configured source is tried. Only valid OHLC bars are stored.
    """
    policy = retry or RetryPolicy()
    result = FetchResult(symbol=instrument.symbol)
    effective_start = start
    if not full and effective_start is None:
        effective_start = _increment_start(db, instrument, overlap_days)

    for spec in instrument.sources:
        source, source_symbol = resolve(spec)
        logger.info("fetch %s from %s (%s)", instrument.symbol, source.name, source_symbol)

        def attempt(_source=source, _symbol=source_symbol) -> list[Bar]:
            return _source.fetch(_symbol)

        try:
            bars = call_with_retry(
                attempt,
                policy,
                on_retry=lambda attempt_no, exc, delay: logger.warning(
                    "source %s attempt %s failed: %s; retrying in %.1fs",
                    source.name,
                    attempt_no,
                    exc,
                    delay,
                ),
            )
        except SourceError as exc:
            message = f"{source.name}:{source_symbol} 获取失败: {exc}"
            logger.warning(message)
            result.errors.append(message)
            continue

        selected = _filter_bars(bars, effective_start, end)
        valid: list[Bar] = []
        skipped = 0
        for bar in selected:
            reason = bar.problem()
            if reason:
                skipped += 1
                logger.debug("skip %s %s: %s", instrument.symbol, bar.date, reason)
                continue
            valid.append(bar)

        if not valid:
            if skipped:
                message = f"{source.name}:{source_symbol} 返回的 {skipped} 条数据均无效"
            else:
                message = f"{source.name}:{source_symbol} 无区间内数据"
            logger.warning("%s: %s", instrument.symbol, message)
            result.errors.append(message)
            result.skipped += skipped
            continue

        stored = db.upsert_bars(instrument.symbol, valid, source=f"{source.name}:{source_symbol}")
        result.source = f"{source.name}:{source_symbol}"
        result.bars = valid
        result.stored = stored
        result.skipped = skipped
        logger.info(
            "%s stored %s bars from %s (%s)",
            instrument.symbol,
            stored,
            source.name,
            valid[-1].date,
        )
        return result

    logger.error("%s: all sources failed", instrument.symbol)
    return result


def collect_all(
    db: Database,
    symbols: Sequence[str],
    *,
    full: bool = False,
    start: date | None = None,
    end: date | None = None,
    retry: RetryPolicy | None = None,
    on_symbol: Callable[[int, int, FetchResult], None] | None = None,
) -> list[FetchResult]:
    """Collect every requested symbol; a failure never aborts the others.

    ``on_symbol`` is called after each symbol with ``(done, total, result)`` so
    recorders can publish progress without copying the loop.
    """
    results = []
    total = len(symbols)
    for index, symbol in enumerate(symbols):
        instrument = get_instrument(symbol)
        result = collect_instrument(
            db,
            instrument,
            full=full,
            start=start,
            end=end,
            retry=retry,
        )
        results.append(result)
        if on_symbol is not None:
            on_symbol(index + 1, total, result)
    return results
