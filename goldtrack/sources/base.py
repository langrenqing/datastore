"""Source protocol and retry helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

import requests

from ..models import Bar
from .http import FetchError

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


def is_retryable_error(exc: BaseException) -> bool:
    """Return whether ``exc`` is a transient failure worth retrying.

    Only network failures and errors the data sources explicitly classify as
    recoverable are retried.  Program errors such as ``ValueError`` or
    ``KeyError`` are not: retrying them only delays the real fix and hides the
    original traceback behind a generic "source failed" message.
    """
    return isinstance(exc, (SourceError, FetchError, requests.RequestException))


def call_with_retry(
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    retry_if: Callable[[BaseException], bool] = is_retryable_error,
) -> T:
    """Call ``func``, retrying transient failures with exponential backoff.

    Failures rejected by ``retry_if`` fail fast but are still wrapped in
    :class:`SourceError` so callers keep their existing "record the failure and
    continue with the next source/day" behaviour.  The original exception is
    preserved as ``__cause__``.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - classified by retry_if
            last_error = exc
            if not retry_if(exc):
                raise SourceError(f"{type(exc).__name__}: {exc}") from exc
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise SourceError(
        str(last_error) if last_error else "未知错误"
    ) from last_error
