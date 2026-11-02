"""Plain-text trend report for the terminal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from .db import Database
from .instruments import get_instrument
from .indicators import Snapshot, bars_to_frame, compute_indicators, latest_snapshot
from .text import render_table


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}%"


@dataclass
class ReportRow:
    symbol: str
    name: str
    snapshot: Snapshot
    first_date: date
    count: int


def _collect_rows(db: Database, symbols: Sequence[str]) -> list[ReportRow]:
    rows: list[ReportRow] = []
    for symbol in symbols:
        instrument = get_instrument(symbol)
        bars = db.query(symbol)
        if not bars:
            continue
        frame = compute_indicators(bars_to_frame(bars))
        snapshot = latest_snapshot(frame)
        if snapshot is None:
            continue
        rows.append(
            ReportRow(
                symbol=symbol,
                name=instrument.name,
                snapshot=snapshot,
                first_date=bars[0].date,
                count=len(bars),
            )
        )
    return rows


REPORT_WIDTHS = (14, 12, 12, 10, 11, 11, 11, 10, 6, 6)
REPORT_ALIGNS = ("left", "left", "right", "right", "right", "right", "right", "right", "left", "right")


def build_report(db: Database, symbols: Sequence[str]) -> str:
    """Build an aligned Chinese text report, with a note when data is missing."""
    rows = _collect_rows(db, symbols)
    if not rows:
        return "暂无数据，请先运行 `python -m goldtrack fetch`。"

    latest = max(row.snapshot.date for row in rows)
    headers = ["品种", "最新日期", "收盘", "日涨跌", "MA5", "MA20", "MA60", "20日波动", "趋势", "样本"]
    body = []
    for row in rows:
        snap = row.snapshot
        body.append(
            [
                f"{row.symbol} {row.name}",
                str(snap.date),
                _price(snap.close),
                _pct(snap.daily_change_pct),
                _price(snap.ma5),
                _price(snap.ma20),
                _price(snap.ma60),
                _pct(snap.volatility_pct),
                snap.trend,
                str(row.count),
            ]
        )
    table = render_table(headers, body, REPORT_WIDTHS, REPORT_ALIGNS)
    lines = [f"黄金价格趋势跟踪  数据截至 {latest}", ""] + table

    missing = [symbol for symbol in symbols if symbol not in {row.symbol for row in rows}]
    if missing:
        lines.append("")
        lines.append(f"缺少数据：{', '.join(missing)}（运行 `python -m goldtrack fetch` 采集）")

    lines.append("")
    lines.append("说明：MA5/MA20/MA60 为收盘价均线；20日波动为年化波动率；趋势基于均线排列判定。")
    for row in rows:
        lines.append(
            f"数据范围：{row.symbol} {row.first_date} ~ {row.snapshot.date}，共 {row.count} 根日线"
        )
    return "\n".join(lines)

