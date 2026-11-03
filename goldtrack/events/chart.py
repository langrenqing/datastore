"""Overlay chart: gold price trend and GDELT event timeline."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")  # noqa: E402 - must be set before pyplot import

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from ..chart import configure_fonts  # noqa: E402
from ..config import CHART_DPI, CHART_FIGSIZE  # noqa: E402
from ..models import Instrument  # noqa: E402
from .alignment import align_events, as_date, trading_days  # noqa: E402
from .models import GeoEvent  # noqa: E402
from .severity import TYPE_COLORS  # noqa: E402
from .watchlist import ALL_REGIONS  # noqa: E402

logger = logging.getLogger(__name__)

PRICE_COLOR = "#1f2d3d"
MA20_COLOR = "#2980b9"
TOP_EVENTS = 4


def _bubble_size(intensity: float) -> float:
    """Marker area that grows with intensity but stays readable when dense."""
    return 18.0 + max(0.0, min(intensity, 100.0)) * 1.6


def _type_handles(types: Sequence[str]) -> list[Line2D]:
    return [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            markerfacecolor=TYPE_COLORS.get(name, TYPE_COLORS["其他"]),
            markeredgecolor="white",
            markersize=8,
            label=name,
        )
        for name in types
    ]


def _strongest_by_day(
    aligned: Sequence[tuple[GeoEvent, date]],
) -> dict[date, GeoEvent]:
    strongest: dict[date, GeoEvent] = {}
    for event, market_date in aligned:
        current = strongest.get(market_date)
        if current is None or event.intensity > current.intensity:
            strongest[market_date] = event
    return strongest


def plot_event_chart(
    frame: pd.DataFrame,
    events: Sequence[GeoEvent],
    instrument: Instrument,
    out_path: str | Path,
    *,
    days: int = 180,
) -> Path:
    """Render the price line together with event bubbles and a region timeline."""
    if frame.empty:
        raise ValueError(f"{instrument.symbol} 没有可绘制的数据")
    if not events:
        raise ValueError("没有事件数据，请先运行 `python -m goldtrack events fetch`")

    configure_fonts()
    data = frame.sort_values("date").tail(days).reset_index(drop=True).copy()
    data["date"] = pd.to_datetime(data["date"])
    window_days = trading_days(data["date"])
    all_days = trading_days(frame["date"])
    window_start, window_end = window_days[0], window_days[-1]
    aligned = [
        (event, market_date)
        for event, market_date in align_events(events, all_days)
        if window_start <= market_date <= window_end
    ]
    if not aligned:
        raise ValueError("所选时间段没有可对齐到交易日的事件")

    by_day = _strongest_by_day(aligned)
    price_by_day = {
        as_date(row["date"]): float(row["close"]) for _, row in data.iterrows()
    }
    timeline: dict[tuple[str, date], GeoEvent] = {}
    for event, market_date in aligned:
        key = (event.region, market_date)
        current = timeline.get(key)
        if current is None or event.intensity > current.intensity:
            timeline[key] = event

    figure, (price_ax, timeline_ax) = plt.subplots(
        2,
        1,
        figsize=(CHART_FIGSIZE[0], CHART_FIGSIZE[1] + 1.0),
        dpi=CHART_DPI,
        sharex=True,
        gridspec_kw={"height_ratios": [3.3, 1.5], "hspace": 0.08},
        layout="constrained",
    )

    price_ax.plot(
        data["date"], data["close"], color=PRICE_COLOR, linewidth=1.8, label="收盘价"
    )
    if "ma20" in data.columns and data["ma20"].notna().any():
        price_ax.plot(
            data["date"],
            data["ma20"],
            color=MA20_COLOR,
            linewidth=1.1,
            alpha=0.8,
            label="MA20",
        )

    days_sorted = sorted(by_day)
    xs = [pd.Timestamp(day) for day in days_sorted]
    ys = [price_by_day.get(day) for day in days_sorted]
    valid = [(x, y, by_day[day]) for x, y, day in zip(xs, ys, days_sorted) if y is not None]
    if valid:
        price_ax.scatter(
            [item[0] for item in valid],
            [item[1] for item in valid],
            s=[_bubble_size(item[2].intensity) for item in valid],
            c=[TYPE_COLORS.get(item[2].event_type, TYPE_COLORS["其他"]) for item in valid],
            alpha=0.55,
            edgecolors="white",
            linewidths=0.5,
            zorder=5,
        )

    top_events = sorted(
        by_day.values(), key=lambda event: event.intensity, reverse=True
    )[:TOP_EVENTS]
    for rank, event in enumerate(top_events):
        market_date = next(day for day, item in by_day.items() if item is event)
        price = price_by_day.get(market_date)
        if price is None:
            continue
        offset = 24 if rank % 2 == 0 else -34
        label = f"{event.occurred_at:%m-%d} {event.country} {event.event_type}"
        price_ax.annotate(
            label,
            xy=(pd.Timestamp(market_date), price),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="#34495e",
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "edgecolor": "#d5d8dc",
                "alpha": 0.85,
            },
            arrowprops={"arrowstyle": "-", "color": "#95a5a6", "linewidth": 0.6},
        )

    start_text = data["date"].iloc[0].strftime("%Y-%m-%d")
    end_text = data["date"].iloc[-1].strftime("%Y-%m-%d")
    price_ax.set_title(
        f"{instrument.name}（{instrument.unit}） × 地缘政治事件  "
        f"{start_text} ~ {end_text}  事件 {len(aligned):,} 个",
        fontsize=14,
        pad=12,
    )
    price_ax.set_ylabel(f"价格（{instrument.unit}）")
    price_ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.45)
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))

    types_present = [
        name for name in TYPE_COLORS if any(event.event_type == name for event, _ in aligned)
    ]
    handles = [
        Line2D([], [], color=PRICE_COLOR, linewidth=1.8, label="收盘价"),
        Line2D([], [], color=MA20_COLOR, linewidth=1.1, label="MA20"),
    ] + _type_handles(types_present)
    price_ax.legend(
        handles=handles,
        loc="upper right",
        frameon=True,
        framealpha=0.85,
        ncol=4,
        fontsize=9,
    )

    regions_present = [region for region in ALL_REGIONS if any(r == region for r, _ in timeline)]
    region_index = {region: index for index, region in enumerate(regions_present)}
    for (region, market_date), event in timeline.items():
        timeline_ax.scatter(
            pd.Timestamp(market_date),
            region_index[region],
            s=4.0 + _bubble_size(event.intensity) * 0.10,
            c=TYPE_COLORS.get(event.event_type, TYPE_COLORS["其他"]),
            alpha=0.75,
            edgecolors="white",
            linewidths=0.35,
            zorder=4,
        )
    timeline_ax.set_yticks(range(len(regions_present)))
    timeline_ax.set_yticklabels(regions_present, fontsize=9)
    timeline_ax.set_ylim(len(regions_present) - 0.4, -0.6)
    timeline_ax.set_ylabel("地区")
    timeline_ax.grid(True, axis="x", linestyle="--", linewidth=0.5, alpha=0.35)
    timeline_ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    timeline_ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    timeline_ax.set_xlabel("事件日期（已对齐到有收盘价的交易日）")

    price_ax.text(
        0.01,
        0.02,
        "气泡大小=规则强度，颜色=事件类型；数据源 GDELT + 新浪财经。"
        "仅保留关注区域与全球重大事件，强度不代表因果影响。",
        transform=price_ax.transAxes,
        fontsize=7.5,
        color="#7f8c8d",
        va="bottom",
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, bbox_inches="tight")
    plt.close(figure)
    logger.info("event chart written to %s", out_path)
    return out_path


def default_event_chart_path(
    chart_dir: Path, symbol: str, end: date | None, days: int
) -> Path:
    """Build a stable filename such as ``events_XAU_2026-11-02_180d.png``."""
    stamp = end.isoformat() if end else "latest"
    return Path(chart_dir) / f"events_{symbol}_{stamp}_{days}d.png"
