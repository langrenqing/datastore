"""SQLite storage for daily bars and instrument metadata."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

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

CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date);
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
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self, instruments: Sequence[Instrument] = ()) -> "Database":
        """Create tables and register instrument metadata."""
        with self.connect() as conn:
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
