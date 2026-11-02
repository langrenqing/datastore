"""Source protocol and retry helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

from ..models import Bar

T = TypeVar("T")


class SourceError(RuntimeError):
    """Raised when a data source cannot provide a usable series."""


class DataSource(Protocol):
    """A daily-bar provider. Implementations must be network-isolated in tests."""

    name: str

    def fetch(self, symbol: str) -> list[Bar]:
        """Return daily bars ordered by date (oldest first)."""
        ...


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings shared by all sources."""

    attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 8.0

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff delay before retry number ``attempt`` (1-based)."""
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)


def call_with_retry(
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Call ``func``, retrying transient failures with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - retried and re-raised below
            last_error = exc
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise SourceError(str(last_error) if last_error else "未知错误")
