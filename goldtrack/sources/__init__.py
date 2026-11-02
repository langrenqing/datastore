"""Source registry: resolves ``source:symbol`` specs from the instrument table."""

from __future__ import annotations

from .base import DataSource, SourceError
from .sge import SgeSource
from .sina import SinaDomesticSource, SinaGlobalSource
from .yahoo import YahooSource

_SOURCES: dict[str, DataSource] = {
    "sina_global": SinaGlobalSource(),
    "sina_domestic": SinaDomesticSource(),
    "sge": SgeSource(),
    "yahoo": YahooSource(),
}


def available_sources() -> dict[str, DataSource]:
    """Return the registered sources (name -> implementation)."""
    return dict(_SOURCES)


def resolve(spec: str) -> tuple[DataSource, str]:
    """Split ``source:symbol`` and return the source instance plus its symbol."""
    source_name, sep, symbol = spec.partition(":")
    if not sep or not source_name or not symbol:
        raise SourceError(f"数据源配置无效: {spec!r}，应为 source:symbol")
    try:
        return _SOURCES[source_name], symbol
    except KeyError:
        known = ", ".join(sorted(_SOURCES))
        raise SourceError(f"未知数据源 {source_name!r}，可用数据源: {known}") from None
