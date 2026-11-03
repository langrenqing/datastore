"""Execute one recorded collection run in a child process.

The Web UI starts this module with ``python -m goldtrack.web.job <run_id>``.
All progress and the final summary are written to ``collection_runs`` so the
server polls a durable record rather than an in-memory job object.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from ..db import Database
from ..instruments import INSTRUMENTS
from ..runs import run_events, run_prices
from ..sources.base import RetryPolicy


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m goldtrack.web.job")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)

    db = Database(args.db).init(INSTRUMENTS.values())
    run = db.get_run(args.run_id)
    if run is None:
        print(f"run {args.run_id} not found", file=sys.stderr)
        return 2

    params = json.loads(run.get("params") or "{}")
    retry = RetryPolicy(
        attempts=int(params.get("retries", 3)),
        base_delay=float(params.get("retry_delay", 1.0)),
    )
    try:
        if run["kind"] == "prices":
            run_prices(
                db,
                run["id"],
                symbols=params.get("symbols") or ["XAU"],
                full=bool(params.get("full", False)),
                start=_parse_date(params.get("start")),
                end=_parse_date(params.get("end")),
                retry=retry,
            )
        elif run["kind"] == "events":
            run_events(
                db,
                run["id"],
                start=_parse_date(params.get("start")),
                end=_parse_date(params.get("end")),
                days=int(params.get("days", 30)),
                min_sources=int(params.get("min_sources", 3)),
                global_min_sources=int(params.get("global_min_sources", 10)),
                all_countries=bool(params.get("all_countries", False)),
                min_intensity=float(params.get("min_intensity", 0.0)),
                retry=retry,
            )
        else:
            db.finish_run(
                run["id"], "failed", error=f"未知任务类型: {run['kind']}"
            )
            return 2
    except Exception as exc:  # run_prices/run_events already recorded failure
        print(f"collection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
