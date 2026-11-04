"""Local FastAPI application serving the JSON API and the static Web UI."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..db import CollectionBusy, Database
from ..instruments import INSTRUMENTS
from . import service
from .jobs import recover_stale_runs, spawn_job
from .schemas import CollectRequest

STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_spawner() -> Callable:
    """Dependency seam so tests can replace the subprocess spawner."""
    return spawn_job


def create_app(db_path: Optional[str | Path] = None) -> FastAPI:
    path = Path(db_path).expanduser() if db_path else config.DEFAULT_DB_PATH
    db = Database(path).init(INSTRUMENTS.values())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        recover_stale_runs(db)
        yield

    app = FastAPI(
        title="goldtrack 数据工作台",
        description="黄金价格与地缘事件的本地浏览、筛选与采集管理。",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.db_path = path

    @app.get("/api/health")
    def api_health():
        return {"ok": True}

    @app.get("/api/meta")
    def api_meta():
        return service.meta_payload()

    @app.get("/api/instruments")
    def api_instruments():
        return {"instruments": service.instrument_cards(db)}

    @app.get("/api/prices")
    def api_prices(
        symbol: str = Query("XAU"),
        days: int = Query(service.DEFAULT_PRICE_DAYS, ge=0, le=10000),
        start: Optional[date] = Query(None),
        end: Optional[date] = Query(None),
        max_points: int = Query(service.DEFAULT_MAX_POINTS, ge=50, le=20000),
    ):
        try:
            return service.prices_payload(
                db,
                symbol,
                days=days,
                start=start,
                end=end,
                max_points=max_points,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/events")
    def api_events(
        symbol: str = Query("XAU"),
        days: int = Query(service.DEFAULT_EVENT_DAYS, ge=1, le=3650),
        start: Optional[date] = Query(None),
        end: Optional[date] = Query(None),
        region: Optional[List[str]] = Query(None),
        min_level: int = Query(1, ge=1, le=5),
        event_type: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        limit: int = Query(service.DEFAULT_PAGE_SIZE, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        try:
            return service.events_payload(
                db,
                start=start,
                end=end,
                days=days,
                regions=region,
                min_level=min_level,
                event_type=event_type,
                keyword=q,
                limit=limit,
                offset=offset,
                symbol=symbol,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/events/overlay")
    def api_overlay(
        symbol: str = Query("XAU"),
        days: int = Query(service.DEFAULT_PRICE_DAYS, ge=0, le=5000),
        start: Optional[date] = Query(None),
        end: Optional[date] = Query(None),
        min_level: int = Query(1, ge=1, le=5),
        max_points: int = Query(service.DEFAULT_MAX_POINTS, ge=50, le=20000),
    ):
        try:
            return service.overlay_payload(
                db,
                symbol,
                days=days,
                start=start,
                end=end,
                min_level=min_level,
                max_points=max_points,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/status")
    def api_status():
        return service.status_payload(db)

    @app.get("/api/jobs")
    def api_jobs(limit: int = Query(20, ge=1, le=100)):
        return {"runs": service.runs_payload(db, limit)}

    @app.get("/api/jobs/{run_id}")
    def api_job(run_id: int):
        payload = service.run_payload(db, db.get_run(run_id))
        if payload is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return payload

    @app.post("/api/collect", status_code=202)
    def api_collect(
        payload: CollectRequest = Body(...),
        spawner: Callable = Depends(get_spawner),
    ):
        data = payload.model_dump(mode="json")
        try:
            run_id = spawner(db, data, path, source="web")
        except CollectionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"无法启动采集进程: {exc}"
            )
        return {"run_id": run_id, "status": "queued"}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m goldtrack.web",
        description="本机 Web UI：浏览金价/事件数据并管理采集",
    )
    parser.add_argument(
        "--db", default=str(config.DEFAULT_DB_PATH), help="SQLite 数据库路径"
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args(argv)

    import uvicorn

    app = create_app(args.db)
    print(f"goldtrack Web UI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
