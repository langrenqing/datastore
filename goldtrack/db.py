"""SQLite storage for daily bars, instrument metadata and GDELT events."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from .events.models import EventImpact, GeoEvent
from .models import Bar, Instrument

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    symbol      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    unit        TEXT NOT NULL,
    currency    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL DEFAULT 0,
    source      TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);

-- The primary key already provides a unique (symbol, date) index; the old
-- explicit index was redundant and only added write cost.  Keep the drop so
-- databases created by earlier versions shed it on the next init().
DROP INDEX IF EXISTS idx_prices_symbol_date;

CREATE TABLE IF NOT EXISTS events (
    source            TEXT NOT NULL,
    source_event_id   TEXT NOT NULL,
    occurred_at       TEXT NOT NULL,
    published_at      TEXT,
    country_code      TEXT NOT NULL DEFAULT '',
    country           TEXT NOT NULL DEFAULT '',
    region            TEXT NOT NULL DEFAULT '',
    lat               REAL,
    lon               REAL,
    event_code        TEXT,
    event_base_code   TEXT,
    root_code         TEXT,
    quad_class        INTEGER,
    event_type        TEXT,
    goldstein         REAL,
    num_mentions      INTEGER NOT NULL DEFAULT 0,
    num_sources       INTEGER NOT NULL DEFAULT 0,
    num_articles      INTEGER NOT NULL DEFAULT 0,
    avg_tone          REAL,
    type_severity     REAL NOT NULL DEFAULT 0,
    scale_score       REAL NOT NULL DEFAULT 0,
    spread_score      REAL NOT NULL DEFAULT 0,
    tone_score        REAL NOT NULL DEFAULT 0,
    region_weight     REAL NOT NULL DEFAULT 0,
    intensity         REAL NOT NULL DEFAULT 0,
    level             INTEGER NOT NULL DEFAULT 1,
    summary           TEXT,
    url               TEXT,
    raw_json          TEXT,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_region_date ON events(region, occurred_at);

CREATE TABLE IF NOT EXISTS event_impacts (
    source            TEXT NOT NULL,
    source_event_id   TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    window_name       TEXT NOT NULL,
    market_date       TEXT NOT NULL,
    est_start         TEXT NOT NULL,
    est_end           TEXT NOT NULL,
    baseline_pct      REAL NOT NULL,
    car_pct           REAL NOT NULL,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (source, source_event_id, symbol, window_name)
);

CREATE INDEX IF NOT EXISTS idx_event_impacts_symbol
    ON event_impacts(symbol, window_name);
"""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _as_iso(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class Database:
    """Thin SQLite wrapper; every operation opens a short-lived connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Wait up to 30s for a competing writer instead of failing immediately.
        conn.execute("PRAGMA busy_timeout = 30000")
        # WAL (enabled in init) makes NORMAL durable enough for this local tool.
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self, instruments: Sequence[Instrument] = ()) -> "Database":
        """Create tables and register instrument metadata."""
        with self.connect() as conn:
            # WAL lets the daily price job and the event backfill share the file.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            for instrument in instruments:
                conn.execute(
                    "INSERT INTO instruments(symbol, name, unit, currency) VALUES(?,?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, "
                    "unit=excluded.unit, currency=excluded.currency",
                    (
                        instrument.symbol,
                        instrument.name,
                        instrument.unit,
                        instrument.currency,
                    ),
                )
        return self

    def upsert_bars(self, symbol: str, bars: Sequence[Bar], source: str | None = None) -> int:
        """Insert or update bars; returns the number of rows written."""
        if not bars:
            return 0
        updated_at = _now_iso()
        rows = [
            (
                symbol,
                bar.date.isoformat(),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                source,
                updated_at,
            )
            for bar in bars
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO prices"
                "(symbol, date, open, high, low, close, volume, source, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, volume=excluded.volume, "
                "source=excluded.source, updated_at=excluded.updated_at",
                rows,
            )
        return len(rows)

    def query(
        self,
        symbol: str,
        start: date | str | None = None,
        end: date | str | None = None,
    ) -> list[Bar]:
        """Return bars for ``symbol`` within the inclusive date range."""
        clauses = ["symbol = ?"]
        params: list[str] = [symbol]
        start_iso = _as_iso(start)
        end_iso = _as_iso(end)
        if start_iso:
            clauses.append("date >= ?")
            params.append(start_iso)
        if end_iso:
            clauses.append("date <= ?")
            params.append(end_iso)
        sql = (
            "SELECT date, open, high, low, close, volume FROM prices "
            f"WHERE {' AND '.join(clauses)} ORDER BY date"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Bar(
                date=date.fromisoformat(row["date"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in rows
        ]

    def latest_date(self, symbol: str) -> date | None:
        """Return the newest stored date for a symbol, if any."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(date) AS latest FROM prices WHERE symbol = ?", (symbol,)
            ).fetchone()
        return date.fromisoformat(row["latest"]) if row and row["latest"] else None

    def count(self, symbol: str) -> int:
        """Return the number of stored bars for a symbol."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM prices WHERE symbol = ?", (symbol,)
            ).fetchone()
        return int(row["total"]) if row else 0

    def symbols(self) -> list[str]:
        """Return symbols that currently have stored data."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM prices ORDER BY symbol"
            ).fetchall()
        return [row["symbol"] for row in rows]

    def instrument(self, symbol: str) -> Instrument | None:
        """Return stored instrument metadata, if registered."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT symbol, name, unit, currency FROM instruments WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        return Instrument(
            symbol=row["symbol"],
            name=row["name"],
            unit=row["unit"],
            currency=row["currency"],
        )

    def upsert_events(self, events: Sequence[GeoEvent]) -> int:
        """Insert or update events; returns the number of rows written."""
        if not events:
            return 0
        fetched_at = _now_iso()
        placeholders = ",".join("?" for _ in EVENT_COLUMNS)
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in EVENT_COLUMNS
            if column not in {"source", "source_event_id"}
        )
        sql = (
            f"INSERT INTO events({','.join(EVENT_COLUMNS)}) VALUES({placeholders}) "
            f"ON CONFLICT(source, source_event_id) DO UPDATE SET {updates}"
        )
        rows = [_event_row(event, fetched_at) for event in events]
        with self.connect() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def query_events(
        self,
        start: date | str | None = None,
        end: date | str | None = None,
        regions: Sequence[str] | None = None,
        min_level: int = 1,
        min_intensity: float = 0.0,
        limit: int | None = None,
    ) -> list[GeoEvent]:
        """Return events in the inclusive date range, oldest first.

        When ``limit`` is set, keep the newest ``limit`` events: dates are
        ranked newest first and, within one date, the strongest events win.
        The returned list is still ordered oldest first.
        """
        clauses = ["level >= ?", "intensity >= ?"]
        params: list = [min_level, min_intensity]
        start_iso = _as_iso(start)
        end_iso = _as_iso(end)
        if start_iso:
            clauses.append("occurred_at >= ?")
            params.append(start_iso)
        if end_iso:
            clauses.append("occurred_at <= ?")
            params.append(end_iso)
        if regions:
            placeholders = ",".join("?" for _ in regions)
            clauses.append(f"region IN ({placeholders})")
            params.extend(regions)
        where = f"WHERE {' AND '.join(clauses)}"
        order_ascending = "ORDER BY occurred_at, intensity DESC, source_event_id"
        if limit is not None and limit > 0:
            # Select the newest rows in SQL so a limit does not materialise the
            # whole range.  occurred_at + intensity DESC ranks the strongest
            # events of the newest date first; the outer query restores the
            # "oldest first" contract.
            sql = (
                "SELECT * FROM ("
                f"SELECT * FROM events {where} "
                "ORDER BY occurred_at DESC, intensity DESC, source_event_id DESC "
                "LIMIT ?"
                ") "
                f"{order_ascending}"
            )
            params.append(limit)
        else:
            sql = f"SELECT * FROM events {where} {order_ascending}"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_event_from_row(row) for row in rows]

    def count_events(self) -> int:
        """Return the number of stored events."""
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM events").fetchone()
        return int(row["total"]) if row else 0

    def latest_event_published_at(self) -> date | None:
        """Return the newest GDELT source date already stored."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(published_at) AS latest FROM events"
            ).fetchone()
        value = row["latest"] if row else None
        return date.fromisoformat(value[:10]) if value else None

    def event_date_range(self) -> tuple[date | None, date | None]:
        """Return the oldest and newest event occurrence dates."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MIN(occurred_at) AS first, MAX(occurred_at) AS last FROM events"
            ).fetchone()
        if row is None or row["first"] is None:
            return None, None
        return date.fromisoformat(row["first"]), date.fromisoformat(row["last"])

    def upsert_event_impacts(self, impacts: Sequence[EventImpact]) -> int:
        """Insert or update event-window statistics."""
        if not impacts:
            return 0
        rows = [
            (
                impact.source,
                impact.source_event_id,
                impact.symbol,
                impact.window,
                impact.market_date.isoformat(),
                impact.est_start.isoformat(),
                impact.est_end.isoformat(),
                impact.baseline_pct,
                impact.car_pct,
                impact.computed_at,
            )
            for impact in impacts
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO event_impacts"
                "(source, source_event_id, symbol, window_name, market_date, "
                "est_start, est_end, baseline_pct, car_pct, computed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source, source_event_id, symbol, window_name) "
                "DO UPDATE SET market_date=excluded.market_date, "
                "est_start=excluded.est_start, est_end=excluded.est_end, "
                "baseline_pct=excluded.baseline_pct, car_pct=excluded.car_pct, "
                "computed_at=excluded.computed_at",
                rows,
            )
        return len(rows)

    def query_event_impacts(
        self, symbol: str, window: str | None = None
    ) -> list[EventImpact]:
        """Return stored window statistics for one symbol, oldest first."""
        clauses = ["symbol = ?"]
        params: list = [symbol]
        if window is not None:
            clauses.append("window_name = ?")
            params.append(window)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event_impacts "
                f"WHERE {' AND '.join(clauses)} ORDER BY market_date, window_name",
                params,
            ).fetchall()
        return [
            EventImpact(
                source=row["source"],
                source_event_id=row["source_event_id"],
                symbol=row["symbol"],
                window=row["window_name"],
                market_date=date.fromisoformat(row["market_date"]),
                est_start=date.fromisoformat(row["est_start"]),
                est_end=date.fromisoformat(row["est_end"]),
                baseline_pct=row["baseline_pct"],
                car_pct=row["car_pct"],
                computed_at=row["computed_at"],
            )
            for row in rows
        ]


def _event_from_row(row: sqlite3.Row) -> GeoEvent:
    published = row["published_at"]
    return GeoEvent(
        source=row["source"],
        source_event_id=row["source_event_id"],
        occurred_at=date.fromisoformat(row["occurred_at"]),
        published_at=date.fromisoformat(published[:10]) if published else None,
        country_code=row["country_code"],
        country=row["country"],
        region=row["region"],
        lat=row["lat"],
        lon=row["lon"],
        event_code=row["event_code"],
        event_base_code=row["event_base_code"],
        root_code=row["root_code"],
        quad_class=row["quad_class"],
        event_type=row["event_type"],
        goldstein=row["goldstein"],
        num_mentions=row["num_mentions"],
        num_sources=row["num_sources"],
        num_articles=row["num_articles"],
        avg_tone=row["avg_tone"],
        type_severity=row["type_severity"],
        scale_score=row["scale_score"],
        spread_score=row["spread_score"],
        tone_score=row["tone_score"],
        region_weight=row["region_weight"],
        intensity=row["intensity"],
        level=row["level"],
        summary=row["summary"] or "",
        url=row["url"] or "",
        raw_json=row["raw_json"] or "",
    )


EVENT_COLUMNS = (
    "source",
    "source_event_id",
    "occurred_at",
    "published_at",
    "country_code",
    "country",
    "region",
    "lat",
    "lon",
    "event_code",
    "event_base_code",
    "root_code",
    "quad_class",
    "event_type",
    "goldstein",
    "num_mentions",
    "num_sources",
    "num_articles",
    "avg_tone",
    "type_severity",
    "scale_score",
    "spread_score",
    "tone_score",
    "region_weight",
    "intensity",
    "level",
    "summary",
    "url",
    "raw_json",
    "fetched_at",
)


def _event_row(event: GeoEvent, fetched_at: str) -> tuple:
    return (
        event.source,
        event.source_event_id,
        event.occurred_at.isoformat(),
        event.published_at.isoformat() if event.published_at else None,
        event.country_code,
        event.country,
        event.region,
        event.lat,
        event.lon,
        event.event_code,
        event.event_base_code,
        event.root_code,
        event.quad_class,
        event.event_type,
        event.goldstein,
        event.num_mentions,
        event.num_sources,
        event.num_articles,
        event.avg_tone,
        event.type_severity,
        event.scale_score,
        event.spread_score,
        event.tone_score,
        event.region_weight,
        event.intensity,
        event.level,
        event.summary,
        event.url,
        event.raw_json,
        fetched_at,
    )
