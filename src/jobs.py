"""Background pipeline runs for the web app.

Each run is the ordinary CLI in a subprocess (`python -m src.cli --country xx
collect`), so the web app can never behave differently from the command line,
and a long collection (HardwareZone alone asks for 10 s between pages) keeps
going while the page is used, reloaded or closed. Output goes to
logs/job_<code>.log. One job per country at a time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_JOBS: dict[str, dict[str, Any]] = {}  # lives as long as the web server process


def log_path(code: str) -> Path:
    return ROOT / "logs" / f"job_{code}.log"


def running(code: str) -> bool:
    job = _JOBS.get(code)
    return bool(job) and job["proc"].poll() is None


def start(code: str, label: str, args: list[str]) -> None:
    if running(code):
        raise RuntimeError(f"a job is already running for {code}")
    lp = log_path(code)
    lp.parent.mkdir(exist_ok=True)
    fh = open(lp, "w", encoding="utf-8")
    fh.write(f"$ python -m src.cli --country {code} {' '.join(args)}\n")
    fh.flush()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "COLUMNS": "110"}
    proc = subprocess.Popen([sys.executable, "-m", "src.cli", "--country", code, *args],
                            cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, env=env)
    _JOBS[code] = {"proc": proc, "label": label, "started": time.time(), "fh": fh}


def stop(code: str) -> None:
    job = _JOBS.get(code)
    if job and job["proc"].poll() is None:
        job["proc"].terminate()


def status(code: str) -> dict[str, Any] | None:
    job = _JOBS.get(code)
    if not job:
        return None
    rc = job["proc"].poll()
    if rc is not None and not job["fh"].closed:
        job["fh"].close()
    return {"label": job["label"], "running": rc is None, "returncode": rc,
            "elapsed": time.time() - job["started"]}


def log_tail(code: str, lines: int = 60) -> str:
    lp = log_path(code)
    if not lp.exists():
        return ""
    return "\n".join(lp.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
