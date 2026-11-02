"""Instrument registry.

The default set covers the three products requested for phase one:
international spot gold (XAU/USD), SGE Shanghai gold Au99.99 and SHFE
gold futures main-continuous. ``GC`` (COMEX gold futures) is registered as
an optional fourth product and demonstrates multi-source fallback.
"""

from __future__ import annotations

from .models import Instrument

INSTRUMENTS: dict[str, Instrument] = {
    "XAU": Instrument(
        symbol="XAU",
        name="国际黄金现货 XAU/USD",
        unit="美元/盎司",
        currency="USD",
        sources=("sina_global:XAU",),
    ),
    "AU9999": Instrument(
        symbol="AU9999",
        name="上海金 Au99.99",
        unit="元/克",
        currency="CNY",
        sources=("sge:Au99.99",),
    ),
    "AU0": Instrument(
        symbol="AU0",
        name="沪金期货主力连续",
        unit="元/克",
        currency="CNY",
        sources=("sina_domestic:AU0",),
    ),
    "GC": Instrument(
        symbol="GC",
        name="COMEX 黄金期货",
        unit="美元/盎司",
        currency="USD",
        sources=("sina_global:GC", "yahoo:GC=F"),
    ),
}

DEFAULT_SYMBOLS: tuple[str, ...] = ("XAU", "AU9999", "AU0")


def get_instrument(symbol: str) -> Instrument:
    """Look up a tracked instrument by its short symbol."""
    try:
        return INSTRUMENTS[symbol]
    except KeyError:
        known = ", ".join(sorted(INSTRUMENTS))
        raise KeyError(f"未知品种 {symbol!r}，可用品种: {known}") from None
