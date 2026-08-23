# -*- coding: utf-8 -*-
"""Run one locked, auditable Mae Ping data refresh cycle."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT, build_database
except ModuleNotFoundError:
    from src.init_db import ROOT, build_database


DATABASE = ROOT / "data" / "maeping_weather.db"
STATUS_FILE = ROOT / "data" / "operational_status.json"
RUNTIME = ROOT / "runtime"
LOCK_FILE = RUNTIME / "operational_cycle.lock"
LOG_FILE = RUNTIME / "operational_cycle.jsonl"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def append_log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_step(name: str, command: list[str], timeout: int = 240) -> dict:
    started_at = iso_now()
    started = time.monotonic()
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    bundled_node_modules = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if bundled_node_modules.exists():
        environment["NODE_PATH"] = str(bundled_node_modules)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return {
            "name": name,
            "status": "success" if result.returncode == 0 else "failed",
            "return_code": result.returncode,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output": output[-8000:],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "name": name,
            "status": "failed",
            "return_code": None,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output": f"timeout after {timeout}s: {error}",
        }


def portable_report_command() -> list[str] | None:
    plugin_candidates = sorted(
        (Path.home() / ".codex/plugins/cache/openai-curated-remote/data-analytics").glob("*/skills/build-report/scripts/deliver_portable_artifact.mjs"),
        reverse=True,
    )
    node_candidates = [
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node",
    ]
    system_node = shutil.which("node")
    if system_node:
        node_candidates.append(Path(system_node))
    node = next((path for path in node_candidates if path.exists()), None)
    if not plugin_candidates or node is None:
        return None
    return [
        str(node),
        str(plugin_candidates[0]),
        "--input",
        str(ROOT / "reports/accuracy-readiness/artifact.json"),
        "--output",
        str(ROOT / "reports/accuracy-readiness/report.html"),
    ]


def database_counts() -> dict[str, int]:
    import sqlite3

    connection = sqlite3.connect(DATABASE)
    try:
        return {
            "observations": connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "forecast_history": connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0],
            "latest_forecast_rows": connection.execute(
                "SELECT COUNT(*) FROM forecasts WHERE model_run=(SELECT MAX(model_run) FROM forecasts)"
            ).fetchone()[0],
            "matched_pairs_latest": connection.execute(
                """
                SELECT COALESCE(SUM(pair_count), 0) FROM verification_results
                WHERE computed_at=(SELECT MAX(computed_at) FROM verification_results)
                """
            ).fetchone()[0],
            "grid_cells": connection.execute("SELECT COUNT(*) FROM grid_cells").fetchone()[0],
            "grid_forecast_latest": connection.execute(
                "SELECT COUNT(*) FROM grid_forecasts_latest"
            ).fetchone()[0],
            "grid_estimates_latest": connection.execute(
                "SELECT COUNT(*) FROM grid_estimates_latest"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def execute_cycle(local_only: bool = False) -> dict:
    if not DATABASE.exists():
        build_database(DATABASE)
    python = sys.executable
    commands: list[tuple[str, list[str]]] = [
        ("park_grid", [python, "src/build_park_grid.py", str(DATABASE)])
    ]
    if not local_only:
        commands.extend(
            [
                ("forecast", [python, "src/fetch_open_meteo_baseline.py", str(DATABASE)]),
                ("grid_forecast", [python, "src/fetch_grid_forecast.py", str(DATABASE)]),
                ("tmd_qpe", [python, "src/fetch_tmd_qpe.py", str(DATABASE)]),
                ("observations", [python, "src/fetch_thaiwater_observations.py", str(DATABASE)]),
            ]
        )
    commands.append(
        (
            "quality_gate",
            [python, "src/check_operational_quality.py", str(DATABASE)],
        )
    )
    commands.append(
        (
            "site_estimates",
            [python, "src/build_site_estimates.py", str(DATABASE)],
        )
    )
    commands.append(("verification", [python, "src/evaluate_forecasts.py", str(DATABASE)]))
    commands.append(("bias_corrections", [python, "src/train_bias_corrections.py", str(DATABASE)]))
    commands.append(("system_readiness", [python, "src/build_system_readiness.py", str(DATABASE)]))
    commands.append(
        (
            "accuracy_artifact",
            [
                python,
                "src/build_accuracy_report_artifact.py",
                str(DATABASE),
                "reports/accuracy-readiness/artifact.json",
            ],
        )
    )
    report_command = portable_report_command()
    if report_command:
        commands.append(("accuracy_report", report_command))

    started_at = iso_now()
    steps = [run_step(name, command) for name, command in commands]

    # Write the current cycle status before building the self-contained dashboard
    # so the embedded operational badge reflects this same run rather than N-1.
    failed = [step["name"] for step in steps if step["status"] != "success"]
    payload = {
        "cycle_started_at": started_at,
        "cycle_finished_at": iso_now(),
        "status": "success" if not failed else "partial_failure",
        "local_only": local_only,
        "failed_steps": failed,
        "steps": steps,
        "counts": database_counts(),
    }
    write_json_atomic(STATUS_FILE, payload)

    if not local_only:
        steps.append(
            run_step(
                "dashboard",
                [python, "src/build_dashboard.py", str(DATABASE), "dashboard/index.html"],
            )
        )
        failed = [step["name"] for step in steps if step["status"] != "success"]
        payload.update(
            {
                "cycle_finished_at": iso_now(),
                "status": "success" if not failed else "partial_failure",
                "failed_steps": failed,
                "steps": steps,
                "counts": database_counts(),
            }
        )
        write_json_atomic(STATUS_FILE, payload)

    append_log(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip network fetches and dashboard boundary refresh; useful for QA.",
    )
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("operational_cycle=skipped reason=already_running")
            return 0
        payload = execute_cycle(local_only=args.local_only)
    print(
        f"operational_cycle={payload['status']} failed_steps={','.join(payload['failed_steps']) or 'none'} "
        f"observations={payload['counts']['observations']} "
        f"forecast_history={payload['counts']['forecast_history']} "
        f"grid_cells={payload['counts']['grid_cells']} "
        f"grid_forecast_latest={payload['counts']['grid_forecast_latest']} "
        f"grid_estimates_latest={payload['counts']['grid_estimates_latest']} "
        f"matched_pairs={payload['counts']['matched_pairs_latest']}"
    )
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
