"""HTTP helpers shared by the data sources."""

from __future__ import annotations

from typing import Mapping

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0


class FetchError(RuntimeError):
    """Raised when a remote source cannot be read."""


def get_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """GET a URL and return decoded text, raising :class:`FetchError` on failure."""
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    try:
        response = requests.get(url, headers=merged, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"请求失败: {exc}") from exc
    return response.text


def get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
):
    """GET a URL and decode the JSON body."""
    import json

    text = get_text(url, headers=headers, timeout=timeout)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise FetchError(f"响应不是合法 JSON: {exc}") from exc
