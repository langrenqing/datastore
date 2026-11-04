"""Command line interface for the gold trend tracker."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from . import config
from .chart import default_chart_path, plot_chart
from .db import CollectionBusy, Database
from .events.alignment import align_events, as_date, trading_days
from .events.analysis import WINDOWS, compute_event_impacts, summarize_impacts
from .events.chart import default_event_chart_path, plot_event_chart
from .events.fetch import collect_events
from .events.severity import level_name
from .events.watchlist import ALL_REGIONS
from .fetch import collect_all
from .instruments import DEFAULT_SYMBOLS, INSTRUMENTS, get_instrument
from .indicators import bars_to_frame, compute_indicators
from .report import build_report
from .runs import event_params, price_params, run_events, run_prices
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
    run_id = None
    if getattr(args, "record_run", False):
        try:
            run_id = db.create_run(
                "prices",
                price_params(
                    args.symbols, full=args.full, start=args.start, end=args.end
                ),
                source="cli",
            )
        except CollectionBusy as exc:
            print(f"跳过采集：{exc}")
            return 0
    if run_id is not None:
        results = run_prices(
            db,
            run_id,
            symbols=args.symbols,
            full=args.full,
            start=args.start,
            end=args.end,
            retry=retry,
        )
    else:
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


def _utc_today() -> date:
    return datetime.now(tz=timezone.utc).date()


def _event_market_dates(db: Database, symbol: str, events) -> dict[str, date]:
    bars = db.query(symbol)
    if not bars:
        return {}
    days = trading_days([bar.date for bar in bars])
    return {
        event.source_event_id: market_date
        for event, market_date in align_events(events, days)
    }


def _event_record(event, market_date: date | None) -> dict:
    return {
        "source": event.source,
        "source_event_id": event.source_event_id,
        "occurred_at": event.occurred_at.isoformat(),
        "market_date": market_date.isoformat() if market_date else None,
        "published_at": event.published_at.isoformat() if event.published_at else None,
        "country_code": event.country_code,
        "country": event.country,
        "region": event.region,
        "event_code": event.event_code,
        "root_code": event.root_code,
        "event_type": event.event_type,
        "level": event.level,
        "intensity": event.intensity,
        "type_severity": event.type_severity,
        "scale_score": event.scale_score,
        "spread_score": event.spread_score,
        "tone_score": event.tone_score,
        "region_weight": event.region_weight,
        "num_mentions": event.num_mentions,
        "num_sources": event.num_sources,
        "avg_tone": event.avg_tone,
        "summary": event.summary,
        "url": event.url,
    }


def cmd_events_fetch(args) -> int:
    db = _db(args)
    retry = RetryPolicy(attempts=args.retries, base_delay=args.retry_delay)
    run_id = None
    if getattr(args, "record_run", False):
        try:
            run_id = db.create_run(
                "events",
                event_params(
                    start=args.start,
                    end=args.end,
                    days=args.days,
                    min_sources=args.min_sources,
                    global_min_sources=args.global_min_sources,
                    all_countries=args.all_countries,
                    min_intensity=args.min_intensity,
                ),
                source="cli",
            )
        except CollectionBusy as exc:
            print(f"跳过采集：{exc}")
            return 0
    if run_id is not None:
        summary = run_events(
            db,
            run_id,
            start=args.start,
            end=args.end,
            days=args.days,
            min_sources=args.min_sources,
            global_min_sources=args.global_min_sources,
            all_countries=args.all_countries,
            min_intensity=args.min_intensity,
            retry=retry,
        )
    else:
        summary = collect_events(
            db,
            start=args.start,
            end=args.end,
            days=args.days,
            min_sources=args.min_sources,
            global_min_sources=args.global_min_sources,
            all_countries=args.all_countries,
            min_intensity=args.min_intensity,
            retry=retry,
        )
    if not summary.results:
        print("事件采集：没有需要更新的日期（GDELT 日文件通常在次日提供）。")
    else:
        print(f"事件采集：{summary.start} ~ {summary.end}")
        ok = summary.days - len(summary.failures)
        print(
            f"  日文件 {ok}/{summary.days} 成功，解析 {summary.parsed:,} 条，"
            f"关注事件 {summary.relevant:,} 条，写入/更新 {summary.stored:,} 条"
        )
        for failure in summary.failures:
            print(f"  失败 {failure.day}: {failure.error}")
    print(f"事件库当前共 {db.count_events():,} 条")
    return 1 if summary.failures else 0


def cmd_events_query(args) -> int:
    db = _db(args)
    end = args.end or _utc_today()
    start = args.start or end - timedelta(days=args.days - 1)
    events = db.query_events(
        start=start,
        end=end,
        regions=args.regions,
        min_level=args.level,
        min_intensity=args.min_intensity,
        limit=args.limit,
    )
    market_dates = _event_market_dates(db, args.symbol, events)
    if args.json:
        records = [
            _event_record(event, market_dates.get(event.source_event_id))
            for event in reversed(events)
        ]
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    if not events:
        print("没有查询到事件。")
        return 0

    headers = ["日期", "市场日", "地区", "类型", "等级", "强度", "国家", "摘要"]
    widths = (12, 12, 14, 10, 8, 6, 8, 46)
    aligns = ("left", "left", "left", "left", "left", "right", "left", "left")
    body = []
    for event in reversed(events):
        market_date = market_dates.get(event.source_event_id)
        body.append(
            [
                event.occurred_at.isoformat(),
                market_date.isoformat() if market_date else "-",
                event.region,
                event.event_type,
                f"{event.level} {level_name(event.level)}",
                f"{event.intensity:.1f}",
                event.country,
                event.summary,
            ]
        )
    print("\n".join(render_table(headers, body, widths, aligns)))
    print()
    print(f"共显示 {len(events)} 条事件（{start} ~ {end}）。")
    return 0


def cmd_events_chart(args) -> int:
    db = _db(args)
    bars = db.query(args.symbol)
    if not bars:
        print(f"{args.symbol} 没有价格数据，请先运行 `python -m goldtrack fetch`。")
        return 1
    instrument = get_instrument(args.symbol)
    frame = compute_indicators(bars_to_frame(bars))
    data = frame.tail(args.days)
    start = as_date(data["date"].iloc[0])
    events = db.query_events(
        start=start - timedelta(days=7), end=bars[-1].date
    )
    out = Path(args.out) if args.out else default_event_chart_path(
        Path(args.chart_dir), args.symbol, bars[-1].date, args.days
    )
    try:
        path = plot_event_chart(frame, events, instrument, out, days=args.days)
    except ValueError as exc:
        print(f"事件图表生成失败: {exc}")
        return 1
    print(f"事件图表已生成: {path}")
    return 0


def cmd_events_windows(args) -> int:
    db = _db(args)
    bars = db.query(args.symbol)
    if not bars:
        print(f"{args.symbol} 没有价格数据，请先运行 `python -m goldtrack fetch`。")
        return 1
    frame = compute_indicators(bars_to_frame(bars))
    data = frame.tail(args.days)
    start = as_date(data["date"].iloc[0])
    end = bars[-1].date
    events = db.query_events(start=start, end=end, min_level=args.min_level)
    if not events:
        print("没有可分析的事件，请先运行 `python -m goldtrack events fetch`。")
        return 1

    impacts = compute_event_impacts(events, bars, args.symbol)
    if args.store:
        db.upsert_event_impacts(impacts)
    summary = summarize_impacts(impacts, events)
    computed = {(impact.source, impact.source_event_id) for impact in impacts}

    if args.json:
        payload = {
            "symbol": args.symbol,
            "price_start": start.isoformat(),
            "price_end": end.isoformat(),
            "selected_events": len(events),
            "computed_events": len(computed),
            "windows": [name for name, _ in WINDOWS],
            "summary": [
                {
                    "window": row.window,
                    "level": row.level,
                    "count": row.count,
                    "mean_car": round(row.mean_car, 4),
                    "median_car": round(row.median_car, 4),
                    "positive_share": round(row.positive_share, 4),
                }
                for row in summary
            ],
            "impacts": [
                {
                    "source_event_id": impact.source_event_id,
                    "window": impact.window,
                    "market_date": impact.market_date.isoformat(),
                    "est_start": impact.est_start.isoformat(),
                    "est_end": impact.est_end.isoformat(),
                    "baseline_pct": impact.baseline_pct,
                    "car_pct": impact.car_pct,
                }
                for impact in impacts
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"事件窗口统计（{args.symbol}，{start} ~ {end}）")
    print(
        f"选中事件 {len(events)} 个，其中 {len(computed)} 个具备完整的估计窗口"
        f"（[-120,-21] 交易日）与事件窗口。"
    )
    print()
    headers = ["窗口", "等级", "事件数", "平均CAR", "中位CAR", "CAR>0占比"]
    widths = (8, 8, 7, 10, 10, 10)
    aligns = ("left", "left", "right", "right", "right", "right")
    body = []
    for row in summary:
        level_text = "全部" if row.level is None else f"{row.level} {level_name(row.level)}"
        body.append(
            [
                row.window,
                level_text,
                str(row.count),
                f"{row.mean_car:+.2f}%",
                f"{row.median_car:+.2f}%",
                f"{row.positive_share * 100:.1f}%",
            ]
        )
    print("\n".join(render_table(headers, body, widths, aligns)))

    chosen_window = WINDOWS[1][0] if len(WINDOWS) > 1 else WINDOWS[0][0]
    chosen = {
        impact.source_event_id: impact
        for impact in impacts
        if impact.window == chosen_window
    }
    event_by_id = {event.source_event_id: event for event in events}
    ranked = []
    seen_dates = set()
    for impact in sorted(
        chosen.values(), key=lambda item: abs(item.car_pct), reverse=True
    ):
        if impact.market_date in seen_dates:
            continue
        seen_dates.add(impact.market_date)
        ranked.append(impact)
        if len(ranked) >= args.top:
            break
    if ranked:
        print()
        print(f"绝对值最大的事件窗口（{chosen_window}，交易日）：")
        headers = ["市场日", "地区", "类型", "等级", "强度", "CAR"]
        widths = (12, 14, 10, 8, 6, 10)
        aligns = ("left", "left", "left", "left", "right", "right")
        body = []
        for impact in ranked:
            event = event_by_id.get(impact.source_event_id)
            if event is None:
                continue
            body.append(
                [
                    impact.market_date.isoformat(),
                    event.region,
                    event.event_type,
                    f"{event.level} {level_name(event.level)}",
                    f"{event.intensity:.1f}",
                    f"{impact.car_pct:+.2f}%",
                ]
            )
        print("\n".join(render_table(headers, body, widths, aligns)))
    print()
    print(
        "说明：CAR 为以 [-120,-21] 交易日均值收益为基准的累计异常收益，"
        "仅作描述性统计，不代表因果影响；事件聚集时不能按独立样本解读。"
    )
    return 0


def cmd_web(args) -> int:
    """Start the local Web UI (same app as ``python -m goldtrack.web``)."""
    import uvicorn

    from .web.app import create_app

    app = create_app(args.db)
    print(f"goldtrack Web UI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goldtrack",
        description="黄金价格趋势跟踪（日线、指标、图表）与 GDELT 地缘政治事件",
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

    web_parser = subparsers.add_parser("web", help="启动本机 Web UI")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    web_parser.add_argument("--port", type=int, default=8000, help="监听端口")
    web_parser.set_defaults(func=cmd_web)

    report_parser = subparsers.add_parser("report", help="打印最新趋势报告")
    report_parser.add_argument(
        "--symbols", type=_parse_symbols, default=list(DEFAULT_SYMBOLS)
    )
    report_parser.set_defaults(func=cmd_report)

    run_parser = subparsers.add_parser("run", help="采集 + 报告 + 图表（适合定时任务）")
    add_fetch_options(run_parser)
    run_parser.add_argument("--days", type=_positive_int, default=config.DEFAULT_CHART_DAYS)
    run_parser.add_argument("--chart-dir", default=str(config.DEFAULT_CHART_DIR))
    run_parser.add_argument(
        "--record-run",
        action="store_true",
        help="记录采集运行状态，供 Web UI 查看（cron 脚本使用）",
    )
    run_parser.set_defaults(func=cmd_run)

    events_parser = subparsers.add_parser("events", help="地缘政治事件（GDELT）")
    event_subparsers = events_parser.add_subparsers(
        dest="events_command", required=True
    )

    event_fetch = event_subparsers.add_parser(
        "fetch", help="采集 GDELT 日文件并入库（默认增量）"
    )
    event_fetch.add_argument(
        "--days", type=_positive_int, default=30, help="空库时回填的自然日数"
    )
    event_fetch.add_argument("--start", type=_parse_date, help="起始日期 YYYY-MM-DD")
    event_fetch.add_argument("--end", type=_parse_date, help="结束日期 YYYY-MM-DD")
    event_fetch.add_argument(
        "--min-sources", type=_positive_int, default=3, help="关注区域事件的最少信源数"
    )
    event_fetch.add_argument(
        "--global-min-sources",
        type=_positive_int,
        default=10,
        help="非关注区域全球重大事件的最少信源数",
    )
    event_fetch.add_argument(
        "--all-countries", action="store_true", help="不过滤国家，保留所有显著事件"
    )
    event_fetch.add_argument(
        "--min-intensity", type=_non_negative_float, default=0.0, help="最低强度分"
    )
    event_fetch.add_argument("--retries", type=_positive_int, default=3, help="每天重试次数")
    event_fetch.add_argument(
        "--retry-delay", type=_non_negative_float, default=1.0, help="首次重试等待秒数"
    )
    event_fetch.add_argument(
        "--record-run",
        action="store_true",
        help="记录采集运行状态，供 Web UI 查看（cron 脚本使用）",
    )
    event_fetch.set_defaults(func=cmd_events_fetch)

    event_query = event_subparsers.add_parser("query", help="查询已入库事件")
    event_query.add_argument("--symbol", type=_parse_symbol, default="XAU", help="用于计算市场日的品种")
    event_query.add_argument("--start", type=_parse_date)
    event_query.add_argument("--end", type=_parse_date)
    event_query.add_argument(
        "--days", type=_positive_int, default=90, help="未指定起止日期时回看的自然日数"
    )
    event_query.add_argument(
        "--region",
        dest="regions",
        action="append",
        choices=ALL_REGIONS,
        help="只看一个地区，可重复",
    )
    event_query.add_argument("--level", type=int, choices=range(1, 6), default=1, help="最低等级")
    event_query.add_argument(
        "--min-intensity", type=_non_negative_float, default=0.0, help="最低强度分"
    )
    event_query.add_argument("--limit", type=_positive_int, default=30, help="最多显示条数")
    event_query.add_argument("--json", action="store_true", help="输出 JSON")
    event_query.set_defaults(func=cmd_events_query)

    event_chart = event_subparsers.add_parser(
        "chart", help="生成金价 + 事件叠加图"
    )
    event_chart.add_argument("--symbol", type=_parse_symbol, default="XAU")
    event_chart.add_argument("--days", type=_positive_int, default=config.DEFAULT_CHART_DAYS)
    event_chart.add_argument("--out", help="输出 PNG 路径")
    event_chart.add_argument("--chart-dir", default=str(config.DEFAULT_CHART_DIR))
    event_chart.set_defaults(func=cmd_events_chart)

    event_windows = event_subparsers.add_parser(
        "windows", help="事件窗口异常收益统计（简单事件研究）"
    )
    event_windows.add_argument("--symbol", type=_parse_symbol, default="XAU")
    event_windows.add_argument("--days", type=_positive_int, default=config.DEFAULT_CHART_DAYS)
    event_windows.add_argument(
        "--min-level", type=int, choices=range(1, 6), default=1, help="只统计不低于该等级的事件"
    )
    event_windows.add_argument("--top", type=_positive_int, default=10, help="显示 CAR 绝对值最大的事件数")
    event_windows.add_argument("--json", action="store_true", help="输出 JSON")
    event_windows.add_argument(
        "--no-store", dest="store", action="store_false", help="不写入 event_impacts 表"
    )
    event_windows.set_defaults(func=cmd_events_windows, store=True)

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
