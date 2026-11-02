"""Command line interface for the gold trend tracker."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from . import config
from .chart import default_chart_path, plot_chart
from .db import Database
from .fetch import collect_all
from .instruments import DEFAULT_SYMBOLS, INSTRUMENTS, get_instrument
from .indicators import bars_to_frame, compute_indicators
from .report import build_report
from .sources import available_sources
from .sources.base import RetryPolicy
from .text import render_table

logger = logging.getLogger("goldtrack")


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"应为正整数: {value}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"应为正整数: {value}")
    return number


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"应为非负数字: {value}") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"应为非负数字: {value}")
    return number


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}") from None


def _parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("至少指定一个品种")
    unknown = [symbol for symbol in symbols if symbol not in INSTRUMENTS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"未知品种: {', '.join(unknown)}；可用: {', '.join(sorted(INSTRUMENTS))}"
        )
    return symbols


def _parse_symbol(value: str) -> str:
    symbols = _parse_symbols(value)
    if len(symbols) != 1:
        raise argparse.ArgumentTypeError("只允许一个品种")
    return symbols[0]


def _clean_value(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, date):
        return value.isoformat()
    return value


def _clean_records(records: Sequence[dict]) -> list[dict]:
    return [{key: _clean_value(value) for key, value in record.items()} for record in records]


def _db(args) -> Database:
    # Read-only commands also open the schema so a fresh checkout never crashes.
    return Database(Path(args.db)).init(INSTRUMENTS.values())


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )


def cmd_init(args) -> int:
    db = _db(args)
    print(f"数据库已初始化: {db.path}")
    return 0


def cmd_sources(args) -> int:
    print("可用品种：")
    for symbol, instrument in INSTRUMENTS.items():
        default = "（默认）" if symbol in DEFAULT_SYMBOLS else ""
        print(f"  {symbol:<8} {instrument.name}  [{instrument.unit}]  {default}")
        print(f"           数据源: {', '.join(instrument.sources)}")
    print("\n已注册数据源：")
    for name in sorted(available_sources()):
        print(f"  {name}")
    return 0


def _print_fetch_results(results) -> int:
    print("采集结果：")
    failed = 0
    for result in results:
        if result.source:
            latest = result.bars[-1].date if result.bars else "-"
            print(
                f"  {result.symbol:<8} {result.source:<24} "
                f"写入 {result.stored:>5} 条  最新 {latest}"
                + (f"  跳过无效 {result.skipped} 条" if result.skipped else "")
            )
        else:
            failed += 1
            detail = "; ".join(result.errors) if result.errors else "未知原因"
            print(f"  {result.symbol:<8} 失败: {detail}")
    return 1 if failed else 0


def cmd_fetch(args) -> int:
    db = _db(args)
    db.init(INSTRUMENTS.values())
    retry = RetryPolicy(attempts=args.retries, base_delay=args.retry_delay)
    results = collect_all(
        db,
        args.symbols,
        full=args.full,
        start=args.start,
        end=args.end,
        retry=retry,
    )
    return _print_fetch_results(results)


def cmd_query(args) -> int:
    db = _db(args)
    bars = db.query(args.symbol, start=args.start, end=args.end)
    if args.limit:
        bars = bars[-args.limit :]
    if args.json:
        records = [
            {
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
        print(json.dumps(_clean_records(records), ensure_ascii=False, indent=2, default=str))
        return 0
    if not bars:
        print("没有查询到数据。")
        return 0
    headers = ["日期", "开盘", "最高", "最低", "收盘", "成交量"]
    widths = (12, 11, 11, 11, 11, 13)
    aligns = ("left", "right", "right", "right", "right", "right")
    body = [
        [
            bar.date.isoformat(),
            f"{bar.open:,.2f}",
            f"{bar.high:,.2f}",
            f"{bar.low:,.2f}",
            f"{bar.close:,.2f}",
            f"{bar.volume:,.0f}",
        ]
        for bar in bars
    ]
    print("\n".join(render_table(headers, body, widths, aligns)))
    logger.info("rows=%s", len(bars))
    return 0


def cmd_indicators(args) -> int:
    db = _db(args)
    bars = db.query(args.symbol, start=args.start, end=args.end)
    if not bars:
        print(f"{args.symbol} 没有数据，请先采集。")
        return 1
    frame = compute_indicators(bars_to_frame(bars))
    if args.json:
        columns = [
            "date",
            "close",
            "daily_change_pct",
            "ma5",
            "ma20",
            "ma60",
            "volatility_pct",
            "trend",
        ]
        records = frame[columns].to_dict("records")
        print(json.dumps(_clean_records(records), ensure_ascii=False, indent=2, default=str))
        return 0

    subset = frame.tail(args.limit)

    def number(value, spec):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "-"
        return format(value, spec)

    def percent(value, spec="+.2f"):
        text = number(value, spec)
        return "-" if text == "-" else text + "%"

    headers = ["日期", "收盘", "日涨跌", "MA5", "MA20", "MA60", "20日波动", "趋势"]
    widths = (12, 11, 10, 11, 11, 11, 10, 6)
    aligns = ("left", "right", "right", "right", "right", "right", "right", "left")
    body = [
        [
            row["date"].isoformat(),
            number(row["close"], ",.2f"),
            percent(row["daily_change_pct"]),
            number(row["ma5"], ",.2f"),
            number(row["ma20"], ",.2f"),
            number(row["ma60"], ",.2f"),
            percent(row["volatility_pct"]),
            row["trend"],
        ]
        for _, row in subset.iterrows()
    ]
    print("\n".join(render_table(headers, body, widths, aligns)))
    return 0


def cmd_chart(args) -> int:
    db = _db(args)
    bars = db.query(args.symbol, start=args.start, end=args.end)
    if not bars:
        print(f"{args.symbol} 没有数据，请先采集。")
        return 1
    instrument = get_instrument(args.symbol)
    frame = compute_indicators(bars_to_frame(bars))
    out = Path(args.out) if args.out else default_chart_path(
        Path(args.chart_dir), args.symbol, bars[-1].date, args.days
    )
    path = plot_chart(frame, instrument, out, days=args.days)
    print(f"图表已生成: {path}")
    return 0


def cmd_report(args) -> int:
    db = _db(args)
    print(build_report(db, args.symbols))
    return 0


def cmd_run(args) -> int:
    db = _db(args)
    db.init(INSTRUMENTS.values())
    retry = RetryPolicy(attempts=args.retries, base_delay=args.retry_delay)
    results = collect_all(
        db,
        args.symbols,
        full=args.full,
        start=args.start,
        end=args.end,
        retry=retry,
    )
    code = _print_fetch_results(results)

    print()
    print(build_report(db, args.symbols))

    print()
    for symbol in args.symbols:
        bars = db.query(symbol)
        if not bars:
            continue
        instrument = get_instrument(symbol)
        frame = compute_indicators(bars_to_frame(bars))
        out = default_chart_path(Path(args.chart_dir), symbol, bars[-1].date, args.days)
        try:
            path = plot_chart(frame, instrument, out, days=args.days)
        except ValueError as exc:
            print(f"{symbol} 图表生成失败: {exc}")
            continue
        print(f"图表已生成: {path}")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goldtrack",
        description="黄金价格趋势跟踪（日线采集、存储、指标与图表）",
    )
    parser.add_argument("--db", default=str(config.DEFAULT_DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="初始化数据库")
    init_parser.set_defaults(func=cmd_init)

    sources_parser = subparsers.add_parser("sources", help="列出品种与数据源")
    sources_parser.set_defaults(func=cmd_sources)

    def add_fetch_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--symbols",
            type=_parse_symbols,
            default=list(DEFAULT_SYMBOLS),
            help=f"逗号分隔的品种，默认 {','.join(DEFAULT_SYMBOLS)}",
        )
        sub.add_argument("--full", action="store_true", help="全量重新采集（默认增量）")
        sub.add_argument("--start", type=_parse_date, help="起始日期 YYYY-MM-DD")
        sub.add_argument("--end", type=_parse_date, help="结束日期 YYYY-MM-DD")
        sub.add_argument("--retries", type=_positive_int, default=3, help="每个数据源重试次数")
        sub.add_argument("--retry-delay", type=_non_negative_float, default=1.0, help="首次重试等待秒数")

    fetch_parser = subparsers.add_parser("fetch", help="采集日线数据入库")
    add_fetch_options(fetch_parser)
    fetch_parser.set_defaults(func=cmd_fetch)

    query_parser = subparsers.add_parser("query", help="按时间范围查询历史价格")
    query_parser.add_argument("--symbol", required=True, type=_parse_symbol)
    query_parser.add_argument("--start", type=_parse_date)
    query_parser.add_argument("--end", type=_parse_date)
    query_parser.add_argument("--limit", type=_positive_int, default=0, help="只显示最近 N 条")
    query_parser.add_argument("--json", action="store_true", help="输出 JSON")
    query_parser.set_defaults(func=cmd_query)

    indicators_parser = subparsers.add_parser("indicators", help="计算并打印趋势指标")
    indicators_parser.add_argument("--symbol", required=True, type=_parse_symbol)
    indicators_parser.add_argument("--start", type=_parse_date)
    indicators_parser.add_argument("--end", type=_parse_date)
    indicators_parser.add_argument("--limit", type=_positive_int, default=20, help="显示最近 N 条")
    indicators_parser.add_argument("--json", action="store_true", help="输出 JSON")
    indicators_parser.set_defaults(func=cmd_indicators)

    chart_parser = subparsers.add_parser("chart", help="生成价格与指标图表")
    chart_parser.add_argument("--symbol", required=True, type=_parse_symbol)
    chart_parser.add_argument("--start", type=_parse_date)
    chart_parser.add_argument("--end", type=_parse_date)
    chart_parser.add_argument("--days", type=_positive_int, default=config.DEFAULT_CHART_DAYS)
    chart_parser.add_argument("--out", help="输出 PNG 路径")
    chart_parser.add_argument("--chart-dir", default=str(config.DEFAULT_CHART_DIR))
    chart_parser.set_defaults(func=cmd_chart)

    report_parser = subparsers.add_parser("report", help="打印最新趋势报告")
    report_parser.add_argument(
        "--symbols", type=_parse_symbols, default=list(DEFAULT_SYMBOLS)
    )
    report_parser.set_defaults(func=cmd_report)

    run_parser = subparsers.add_parser("run", help="采集 + 报告 + 图表（适合定时任务）")
    add_fetch_options(run_parser)
    run_parser.add_argument("--days", type=_positive_int, default=config.DEFAULT_CHART_DAYS)
    run_parser.add_argument("--chart-dir", default=str(config.DEFAULT_CHART_DIR))
    run_parser.set_defaults(func=cmd_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"错误: {exc.args[0]}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
