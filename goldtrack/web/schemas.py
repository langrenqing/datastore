"""Request models for the local Web UI API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from ..instruments import INSTRUMENTS


class PricesJob(BaseModel):
    """Start one recorded prices collection."""

    kind: Literal["prices"]
    symbols: List[str] = Field(
        default_factory=lambda: ["XAU", "AU9999", "AU0"], min_length=1
    )
    full: bool = False
    start: Optional[date] = None
    end: Optional[date] = None
    retries: int = Field(3, ge=1, le=10)
    retry_delay: float = Field(1.0, ge=0.0, le=60.0)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, value: List[str]) -> List[str]:
        symbols = [item.strip().upper() for item in value if item.strip()]
        if not symbols:
            raise ValueError("至少选择一个品种")
        unknown = [symbol for symbol in symbols if symbol not in INSTRUMENTS]
        if unknown:
            raise ValueError(
                f"未知品种: {', '.join(unknown)}；可用: {', '.join(sorted(INSTRUMENTS))}"
            )
        # Keep order, drop duplicates.
        return list(dict.fromkeys(symbols))

    @model_validator(mode="after")
    def validate_range(self) -> "PricesJob":
        if self.start and self.end and self.start > self.end:
            raise ValueError("起始日期不能晚于结束日期")
        return self


class EventsJob(BaseModel):
    """Start one recorded GDELT collection."""

    kind: Literal["events"]
    days: int = Field(30, ge=1, le=365)
    start: Optional[date] = None
    end: Optional[date] = None
    min_sources: int = Field(3, ge=1, le=100)
    global_min_sources: int = Field(10, ge=1, le=1000)
    all_countries: bool = False
    min_intensity: float = Field(0.0, ge=0.0, le=100.0)
    retries: int = Field(3, ge=1, le=10)
    retry_delay: float = Field(1.0, ge=0.0, le=60.0)

    @model_validator(mode="after")
    def validate_range(self) -> "EventsJob":
        if self.start and self.end and self.start > self.end:
            raise ValueError("起始日期不能晚于结束日期")
        return self


CollectRequest = Annotated[
    Union[PricesJob, EventsJob], Field(discriminator="kind")
]
