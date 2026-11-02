"""Path and runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(
    os.environ.get("GOLDTRACK_DB", PROJECT_ROOT / "data" / "gold.db")
).expanduser()
DEFAULT_CHART_DIR = Path(
    os.environ.get("GOLDTRACK_CHART_DIR", PROJECT_ROOT / "output" / "charts")
).expanduser()

# Default chart window and output size.
DEFAULT_CHART_DAYS = 180
CHART_FIGSIZE = (14, 8)
CHART_DPI = 150

# Annualisation factor for daily volatility (trading days per year).
ANNUAL_TRADING_DAYS = 252
