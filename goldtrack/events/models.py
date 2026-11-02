"""Data models for the GDELT geopolitical event tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class GdeltRow:
    """One parsed GDELT event export row (daily or 15-minute format)."""

    event_id: str
    day: date
    is_root_event: bool
    event_code: str
    event_base_code: str
    root_code: str
    quad_class: int | None
    goldstein: float | None
    num_mentions: int
    num_sources: int
    num_articles: int
    avg_tone: float | None
    actor1_name: str
    actor1_country: str
    actor2_name: str
    actor2_country: str
    action_country: str
    action_fullname: str
    lat: float | None
    lon: float | None
    url: str


@dataclass(frozen=True)
class GeoEvent:
    """A watchlist event after filtering, scoring and normalisation."""

    source: str
    source_event_id: str
    occurred_at: date
    published_at: date | None
    country_code: str
    country: str
    region: str
    lat: float | None = None
    lon: float | None = None
    event_code: str = ""
    event_base_code: str = ""
    root_code: str = ""
    quad_class: int | None = None
    event_type: str = ""
    goldstein: float | None = None
    num_mentions: int = 0
    num_sources: int = 0
    num_articles: int = 0
    avg_tone: float | None = None
    type_severity: float = 0.0
    scale_score: float = 0.0
    spread_score: float = 0.0
    tone_score: float = 0.0
    region_weight: float = 0.0
    intensity: float = 0.0
    level: int = 1
    summary: str = ""
    url: str = ""
    raw_json: str = ""

    def problem(self) -> str | None:
        """Return a human readable reason when the event is unusable."""
        if not self.source_event_id:
            return "missing source event id"
        if self.occurred_at is None:
            return "missing event date"
        if self.intensity < 0 or self.intensity > 100:
            return f"intensity out of range: {self.intensity}"
        return None


@dataclass(frozen=True)
class EventImpact:
    """Abnormal return around one event for one symbol and window."""

    source: str
    source_event_id: str
    symbol: str
    window: str
    market_date: date
    est_start: date
    est_end: date
    baseline_pct: float
    car_pct: float
    computed_at: str


@dataclass
class DayFetchResult:
    """Outcome of fetching one GDELT daily export file."""

    day: date
    parsed: int = 0
    relevant: int = 0
    stored: int = 0
    error: str | None = None


@dataclass
class EventFetchSummary:
    """Aggregated outcome of one event collection run."""

    start: date | None = None
    end: date | None = None
    results: list[DayFetchResult] = field(default_factory=list)

    @property
    def days(self) -> int:
        return len(self.results)

    @property
    def parsed(self) -> int:
        return sum(result.parsed for result in self.results)

    @property
    def relevant(self) -> int:
        return sum(result.relevant for result in self.results)

    @property
    def stored(self) -> int:
        return sum(result.stored for result in self.results)

    @property
    def failures(self) -> list[DayFetchResult]:
        return [result for result in self.results if result.error]

    @property
    def ok(self) -> bool:
        return not self.failures
