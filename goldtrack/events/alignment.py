"""Align event dates with the trading days available in the price database."""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime
from typing import Iterable, Sequence

from .models import GeoEvent


def as_date(value) -> date:
    """Normalise a ``date``/``datetime``/pandas timestamp to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def trading_days(dates: Iterable) -> list[date]:
    """Return sorted, unique trading dates."""
    return sorted({as_date(value) for value in dates})


def align_market_date(occurred_at: date, days: Sequence[date]) -> date | None:
    """Return the first trading day on or after ``occurred_at``.

    Weekend and holiday events roll forward to the next session with a close;
    events after the newest stored price have no market date yet.
    """
    index = bisect_left(days, occurred_at)
    if index >= len(days):
        return None
    return days[index]


def align_events(
    events: Iterable[GeoEvent], days: Sequence[date]
) -> list[tuple[GeoEvent, date]]:
    """Pair events with their market trading day, dropping unalignable ones."""
    aligned: list[tuple[GeoEvent, date]] = []
    for event in events:
        market_date = align_market_date(event.occurred_at, days)
        if market_date is not None:
            aligned.append((event, market_date))
    return aligned
