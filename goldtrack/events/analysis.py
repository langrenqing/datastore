"""Simple event-window statistics around gold price moves.

This is deliberately the descriptive phase-one analysis: a mean-adjusted
abnormal return (AR) against an estimation window and the cumulative abnormal
return (CAR) over short windows measured in *trading* days.  A full event study
with factor controls, clustering and placebo tests belongs to phase two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from ..indicators import bars_to_frame
from ..models import Bar
from .alignment import align_market_date, as_date
from .models import EventImpact, GeoEvent

# Short event windows expressed in trading days after the market date.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("0,+1", 1),
    ("0,+3", 3),
    ("0,+5", 5),
)
ESTIMATION_START = -120
ESTIMATION_END = -21
MIN_ESTIMATION_DAYS = 40


@dataclass(frozen=True)
class WindowSummary:
    """Aggregated CAR statistics for one window and intensity level."""

    window: str
    level: int | None
    count: int
    mean_car: float
    median_car: float
    positive_share: float


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _returns(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.array([], dtype="float64")
    return (frame["close"].pct_change() * 100.0).to_numpy(dtype="float64")


def compute_event_impacts(
    events: Iterable[GeoEvent],
    bars: Sequence[Bar],
    symbol: str,
    *,
    windows: Sequence[tuple[str, int]] = WINDOWS,
    min_estimation: int = MIN_ESTIMATION_DAYS,
) -> list[EventImpact]:
    """Compute mean-adjusted CAR for every event and window.

    Events without enough price history before or after their market date are
    skipped.  Results are stored per event, symbol and window.
    """
    if not bars or not windows:
        return []
    frame = bars_to_frame(bars)
    if frame.empty:
        return []
    dates = [as_date(value) for value in frame["date"]]
    index_of = {day: index for index, day in enumerate(dates)}
    returns = _returns(frame)
    computed_at = _now_iso()
    impacts: list[EventImpact] = []

    for event in events:
        market_date = align_market_date(event.occurred_at, dates)
        if market_date is None:
            continue
        index = index_of[market_date]
        est_start_index = max(0, index + ESTIMATION_START)
        est_end_index = index + ESTIMATION_END
        if est_end_index < est_start_index:
            continue
        estimation = returns[est_start_index : est_end_index + 1]
        estimation = estimation[np.isfinite(estimation)]
        if estimation.size < min_estimation:
            continue
        baseline = float(np.mean(estimation))

        for window_name, horizon in windows:
            if index + horizon >= returns.size:
                continue
            abnormal = returns[index : index + horizon + 1] - baseline
            if not np.all(np.isfinite(abnormal)):
                continue
            impacts.append(
                EventImpact(
                    source=event.source,
                    source_event_id=event.source_event_id,
                    symbol=symbol,
                    window=window_name,
                    market_date=market_date,
                    est_start=dates[est_start_index],
                    est_end=dates[est_end_index],
                    baseline_pct=round(baseline, 4),
                    car_pct=round(float(np.sum(abnormal)), 4),
                    computed_at=computed_at,
                )
            )
    return impacts


def summarize_impacts(
    impacts: Sequence[EventImpact], events: Iterable[GeoEvent]
) -> list[WindowSummary]:
    """Aggregate CAR by window and intensity level (``None`` means all)."""
    levels = {
        (event.source, event.source_event_id): event.level for event in events
    }
    grouped: dict[tuple[str, int | None], list[float]] = {}
    for impact in impacts:
        grouped.setdefault((impact.window, None), []).append(impact.car_pct)
        level = levels.get((impact.source, impact.source_event_id))
        if level is not None:
            grouped.setdefault((impact.window, level), []).append(impact.car_pct)

    order = {name: index for index, (name, _) in enumerate(WINDOWS)}
    summaries: list[WindowSummary] = []
    for (window_name, level), values in sorted(
        grouped.items(),
        key=lambda item: (
            order.get(item[0][0], 99),
            0 if item[0][1] is not None else 1,
            -(item[0][1] or 0),
        ),
    ):
        array = np.asarray(values, dtype="float64")
        summaries.append(
            WindowSummary(
                window=window_name,
                level=level,
                count=int(array.size),
                mean_car=float(np.mean(array)),
                median_car=float(np.median(array)),
                positive_share=float(np.mean(array > 0)),
            )
        )
    return summaries
