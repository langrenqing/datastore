"""Core data models used across the gold trend tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class Bar:
    """One daily OHLC bar for a single instrument."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def problem(self) -> str | None:
        """Return a human readable reason when the bar is unusable."""
        if self.date is None:
            return "missing date"
        if self.close is None or self.close <= 0:
            return f"invalid close: {self.close!r}"
        if self.high is None or self.low is None:
            return f"missing high/low: {self.high!r}/{self.low!r}"
        if self.high < self.low:
            return f"high {self.high} < low {self.low}"
        if self.open is not None and self.open <= 0:
            return f"invalid open: {self.open!r}"
        return None


@dataclass(frozen=True)
class Instrument:
    """A tracked product and the ordered sources used to fetch it."""

    symbol: str
    name: str
    unit: str
    currency: str
    sources: Sequence[str] = field(default_factory=tuple)


@dataclass
class FetchResult:
    """Outcome of fetching one instrument, including fallback history."""

    symbol: str
    source: str | None = None
    bars: list[Bar] = field(default_factory=list)
    stored: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
