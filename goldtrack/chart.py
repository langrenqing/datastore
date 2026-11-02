"""Matplotlib rendering of price, moving averages, daily change and volatility."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402 - must be set before pyplot import

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .config import CHART_DPI, CHART_FIGSIZE  # noqa: E402
from .indicators import latest_snapshot  # noqa: E402
from .models import Instrument  # noqa: E402

logger = logging.getLogger(__name__)

CJK_FONTS = (
    "PingFang SC",
    "Heiti TC",
    "Hiragino Sans GB",
    "Arial Unicode MS",
    "Songti SC",
    "SimHei",
    "Noto Sans CJK SC",
)
MA_COLORS = {5: "#f39c12", 20: "#2980b9", 60: "#8e44ad"}
UP_COLOR = "#d62728"  # 中国习惯：红涨
DOWN_COLOR = "#2ca02c"  # 绿跌


def configure_fonts() -> str | None:
    """Select the first installed CJK font; return its name if found."""
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for name in CJK_FONTS:
        if name in installed:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    logger.warning("未找到中文字体，图表中的中文可能显示为方块")
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _fmt_price(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def plot_chart(
    frame: pd.DataFrame,
    instrument: Instrument,
    out_path: str | Path,
    *,
    days: int = 180,
) -> Path:
    """Render one instrument's latest ``days`` bars and save a PNG."""
    if frame.empty:
        raise ValueError(f"{instrument.symbol} 没有可绘制的数据")

    configure_fonts()
    data = frame.sort_values("date").tail(days).reset_index(drop=True).copy()
    data["date"] = pd.to_datetime(data["date"])
    snapshot = latest_snapshot(frame)

    figure, (price_ax, indicator_ax) = plt.subplots(
        2,
        1,
        figsize=CHART_FIGSIZE,
        dpi=CHART_DPI,
        sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.2], "hspace": 0.08},
        layout="constrained",
    )

    price_ax.plot(
        data["date"], data["close"], color="#1f2d3d", linewidth=1.8, label="收盘价"
    )
    for window in (5, 20, 60):
        column = f"ma{window}"
        if column in data.columns and data[column].notna().any():
            price_ax.plot(
                data["date"],
                data[column],
                color=MA_COLORS[window],
                linewidth=1.2,
                label=f"MA{window}",
            )

    start_text = data["date"].iloc[0].strftime("%Y-%m-%d")
    end_text = data["date"].iloc[-1].strftime("%Y-%m-%d")
    price_ax.set_title(
        f"{instrument.name}（{instrument.unit}） 日线走势  {start_text} ~ {end_text}",
        fontsize=15,
        pad=12,
    )
    price_ax.set_ylabel(f"价格（{instrument.unit}）")
    price_ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.45)
    price_ax.legend(loc="upper right", frameon=True, framealpha=0.85, ncol=4)
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))

    changes = data["daily_change_pct"].fillna(0.0)
    colors = [UP_COLOR if value >= 0 else DOWN_COLOR for value in changes]
    indicator_ax.bar(
        data["date"], changes, color=colors, width=0.9, alpha=0.75, label="日涨跌幅"
    )
    indicator_ax.axhline(0, color="#666666", linewidth=0.6)
    indicator_ax.set_ylabel("日涨跌幅（%）")
    indicator_ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)

    volatility_ax = indicator_ax.twinx()
    if "volatility_pct" in data.columns:
        volatility_ax.plot(
            data["date"],
            data["volatility_pct"],
            color="#e67e22",
            linewidth=1.3,
            label="20日年化波动率",
        )
    volatility_ax.set_ylabel("波动率（%）", color="#e67e22")
    volatility_ax.tick_params(axis="y", colors="#e67e22")

    if snapshot is not None:
        stats = (
            f"最新 {snapshot.date}  收盘 {_fmt_price(snapshot.close)}  "
            f"日涨跌 {_fmt_pct(snapshot.daily_change_pct)}\n"
            f"MA5 {_fmt_price(snapshot.ma5)}  MA20 {_fmt_price(snapshot.ma20)}  "
            f"MA60 {_fmt_price(snapshot.ma60)}\n"
            f"20日年化波动率 {_fmt_pct(snapshot.volatility_pct)}  趋势 {snapshot.trend}"
        )
        price_ax.text(
            0.015,
            0.98,
            stats,
            transform=price_ax.transAxes,
            va="top",
            ha="left",
            fontsize=10,
            bbox={"boxstyle": "round,pad=0.45", "facecolor": "white", "edgecolor": "#cccccc"},
        )

    indicator_ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    indicator_ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    for label in indicator_ax.get_xticklabels():
        label.set_rotation(0)

    handles_left, labels_left = indicator_ax.get_legend_handles_labels()
    handles_right, labels_right = volatility_ax.get_legend_handles_labels()
    indicator_ax.legend(
        handles_left + handles_right,
        labels_left + labels_right,
        loc="upper right",
        frameon=True,
        framealpha=0.85,
        ncol=2,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, bbox_inches="tight")
    plt.close(figure)
    logger.info("chart written to %s", out_path)
    return out_path


def default_chart_path(chart_dir: Path, symbol: str, end: date | None, days: int) -> Path:
    """Build a stable chart filename such as ``XAU_2026-11-02_180d.png``."""
    stamp = end.isoformat() if end else "latest"
    return Path(chart_dir) / f"{symbol}_{stamp}_{days}d.png"
