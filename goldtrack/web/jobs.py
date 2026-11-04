"""Spawn, monitor and recover background collection jobs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..db import Database

LOG_DIR = config.PROJECT_ROOT / "logs" / "web-jobs"
# A run may be created a moment before its child sets the pid; do not call it
# stale during that window.
QUEUED_GRACE_SECONDS = 120


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pid_alive(pid: int | None) -> bool:
    """Return whether ``pid`` still exists (best effort on POSIX)."""
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) can terminate a process on Windows; leave the row for
        # the user instead of guessing.
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def refresh_run(db: Database, run: dict | None) -> dict | None:
    """Mark a run interrupted when its worker process has disappeared."""
    if run is None or run.get("status") not in ("queued", "running"):
        return run
    if pid_alive(run.get("pid")):
        return run
    started = _parse_ts(run.get("started_at"))
    age = (_now() - started).total_seconds() if started else QUEUED_GRACE_SECONDS
    if not run.get("pid") and age < QUEUED_GRACE_SECONDS:
        return run
    db.mark_run_interrupted(run["id"], "任务进程已退出，状态标记为中断")
    return db.get_run(run["id"]) or run


def recover_stale_runs(db: Database) -> None:
    """Close runs left over from a previous server process."""
    for run in db.list_runs(limit=50):
        if run.get("status") in ("queued", "running"):
            refresh_run(db, run)


def spawn_job(
    db: Database, payload: dict, db_path: str | Path, source: str = "web"
) -> int:
    """Create a run row and start its worker subprocess.

    Collection runs in a child process: GDELT day-file parsing is CPU-heavy and
    must not compete with API requests for the GIL.  The child records its own
    pid and progress in ``collection_runs`` so the server can keep serving even
    if it is restarted mid-run.
    """
    kind = payload["kind"]
    params = {key: value for key, value in payload.items() if key != "kind"}
    run_id = db.create_run(kind, params=params, source=source)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"run-{run_id}.log"
    env = os.environ.copy()
    env["GOLDTRACK_DB"] = str(db_path)
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "goldtrack.web.job",
                    str(run_id),
                    "--db",
                    str(db_path),
                ],
                cwd=str(config.PROJECT_ROOT),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        db.finish_run(
            run_id, "failed", error=f"无法启动采集进程: {exc}"
        )
        raise
    db.set_run_pid(run_id, process.pid)
    # Reap the child when it exits.  Without this a finished process can stay
    # a zombie and ``os.kill(pid, 0)`` keeps reporting it alive, so a crashed
    # run would never be marked interrupted.
    threading.Thread(target=process.wait, daemon=True).start()
    return run_id
