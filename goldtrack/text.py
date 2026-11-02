"""Terminal text alignment helpers that are aware of wide CJK characters."""

from __future__ import annotations

import unicodedata
from typing import Sequence


def display_width(text: str) -> int:
    """Return the terminal display width of ``text`` (CJK counts as 2)."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` display columns, ending with an ellipsis."""
    if display_width(text) <= width:
        return text
    result = ""
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + char_width > width - 1:
            break
        result += char
        used += char_width
    return result + "…"


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad or truncate ``text`` to exactly ``width`` display columns."""
    text = truncate(str(text), width)
    padding = max(width - display_width(text), 0)
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def render_row(cells: Sequence[str], widths: Sequence[int], aligns: Sequence[str]) -> str:
    return "  ".join(
        pad(cell, width, align) for cell, width, align in zip(cells, widths, aligns)
    )


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[int],
    aligns: Sequence[str] | None = None,
) -> list[str]:
    """Render a simple aligned table with a dashed divider."""
    align_list = list(aligns) if aligns is not None else ["left"] * len(widths)
    lines = [
        render_row(headers, widths, align_list),
        "-" * (sum(widths) + 2 * (len(widths) - 1)),
    ]
    for row in rows:
        lines.append(render_row(row, widths, align_list))
    return lines
