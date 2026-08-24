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
    from source_portfolio import output_metric, record_source_health
except ModuleNotFoundError:
    from src.init_db import ROOT, build_database
    from src.source_portfolio import output_metric, record_source_health


DATABASE = ROOT / "data" / "maeping_weather.db"
STATUS_FILE = ROOT / "data" / "operational_status.json"
RUNTIME = ROOT / "runtime"
LOCK_FILE = RUNTIME / "operational_cycle.lock"
LOG_FILE = RUNTIME / "operational_cycle.jsonl"
SOURCE_STAGE_BUDGET_SECONDS = 480
SOURCE_ROUTE_METRICS = {
    "forecast": ("open_meteo_points", "forecast_rows"),
    "grid_forecast": ("open_meteo_grid", "latest_grid_forecast_rows"),
    "tmd_qpe": ("tmd_radar_qpe", "ingested_grid_cells"),
    "observations": ("thaiwater_observations", "observation_rows_upserted"),
    "gistda_flood": ("gistda_flood", "records"),
    "gistda_fire": ("gistda_fire", "records"),
    "usgs_earthquakes": ("usgs_earthquakes", "records"),
}


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


def run_step(
    name: str,
    command: list[str],
    timeout: int = 240,
    extra_environment: dict[str, str] | None = None,
) -> dict:
    started_at = iso_now()
    started = time.monotonic()
    environment = os.environ.copy()
    environment.update(extra_environment or {})
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
            "source_routes": connection.execute("SELECT COUNT(*) FROM source_routes").fetchone()[0],
            "enabled_source_routes": connection.execute(
                "SELECT COUNT(*) FROM source_routes WHERE enabled=1"
            ).fetchone()[0],
            "source_health_latest": connection.execute(
                "SELECT COUNT(*) FROM source_health_latest"
            ).fetchone()[0],
            "hazard_features_latest": connection.execute(
                "SELECT COUNT(*) FROM hazard_features_latest"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def health_already_recorded(route_id: str, cycle_id: str) -> bool:
    import sqlite3

    connection = sqlite3.connect(DATABASE)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM source_health_latest WHERE route_id=? AND cycle_id=?",
            (route_id, cycle_id),
        ).fetchone()[0] > 0
    finally:
        connection.close()


def record_step_health(step: dict, cycle_id: str) -> None:
    route_id, metric_name = SOURCE_ROUTE_METRICS[step["name"]]
    if health_already_recorded(route_id, cycle_id):
        return
    output = step.get("output", "")
    raw_records = output_metric(output, metric_name)
    try:
        records = int(float(raw_records)) if raw_records is not None else None
    except ValueError:
        records = None
    status = "success" if step["status"] == "success" else "failed"
    if step["name"] == "tmd_qpe":
        tmd_status = output_metric(output, "tmd_qpe")
        if tmd_status == "stale":
            status = "stale"
    if status == "success" and records == 0:
        status = "no_data"
    newest = output_metric(output, "newest_source_time") or output_metric(output, "product_time")
    age_hours = output_metric(output, "age_hours")
    try:
        lag = max(0.0, float(age_hours) * 60) if age_hours is not None else None
    except ValueError:
        lag = None
    record_source_health(
        DATABASE,
        route_id=route_id,
        cycle_id=cycle_id,
        status=status,
        duration_seconds=step["duration_seconds"],
        records_received=records,
        newest_source_time=None if newest in (None, "unknown") else newest,
        freshness_lag_minutes=lag,
        error_code=None if step["status"] == "success" else "CONNECTOR_FAILED",
        message=output[-2000:] or status,
    )


def source_health_summary(cycle_id: str) -> dict:
    import sqlite3

    connection = sqlite3.connect(DATABASE)
    try:
        rows = connection.execute(
            """
            SELECT route_id, status, records_received, duration_seconds
            FROM source_health_latest
            WHERE cycle_id=?
            ORDER BY route_id
            """,
            (cycle_id,),
        ).fetchall()
        degraded = [route_id for route_id, status, _, _ in rows if status not in ("success", "no_data")]
        return {
            "routes_checked": len(rows),
            "healthy_routes": sum(status in ("success", "no_data") for _, status, _, _ in rows),
            "degraded_routes": degraded,
            "records_received": sum(records or 0 for _, _, records, _ in rows),
            "duration_seconds_sum": round(sum(duration or 0 for _, _, _, duration in rows), 3),
        }
    finally:
        connection.close()


def budget_exhausted_step(name: str) -> dict:
    return {
        "name": name,
        "status": "failed",
        "return_code": None,
        "started_at": iso_now(),
        "duration_seconds": 0.0,
        "output": f"source_stage_budget_exhausted budget_seconds={SOURCE_STAGE_BUDGET_SECONDS}",
    }


def execute_cycle(local_only: bool = False) -> dict:
    if not DATABASE.exists():
        build_database(DATABASE)
    python = sys.executable
    started_at = iso_now()
    setup_commands: list[tuple[str, list[str], int]] = [
        ("source_portfolio", [python, "src/source_portfolio.py", str(DATABASE)], 60),
        ("park_grid", [python, "src/build_park_grid.py", str(DATABASE)], 60),
    ]
    source_commands: list[tuple[str, list[str], int]] = []
    if not local_only:
        source_commands.extend(
            [
                ("forecast", [python, "src/fetch_open_meteo_baseline.py", str(DATABASE)], 120),
                ("grid_forecast", [python, "src/fetch_grid_forecast.py", str(DATABASE)], 180),
                ("tmd_qpe", [python, "src/fetch_tmd_qpe.py", str(DATABASE)], 90),
                ("observations", [python, "src/fetch_thaiwater_observations.py", str(DATABASE)], 120),
                (
                    "gistda_flood",
                    [python, "src/fetch_gistda_disasters.py", str(DATABASE), "--product", "flood"],
                    90,
                ),
                (
                    "gistda_fire",
                    [python, "src/fetch_gistda_disasters.py", str(DATABASE), "--product", "fire"],
                    90,
                ),
                (
                    "usgs_earthquakes",
                    [python, "src/fetch_usgs_earthquakes.py", str(DATABASE)],
                    60,
                ),
            ]
        )
    processing_commands: list[tuple[str, list[str], int]] = []
    processing_commands.append(
        (
            "quality_gate",
            [python, "src/check_operational_quality.py", str(DATABASE)],
            90,
        )
    )
    processing_commands.append(
        (
            "site_estimates",
            [python, "src/build_site_estimates.py", str(DATABASE)],
            90,
        )
    )
    processing_commands.append(("verification", [python, "src/evaluate_forecasts.py", str(DATABASE)], 90))
    processing_commands.append(("bias_corrections", [python, "src/train_bias_corrections.py", str(DATABASE)], 90))
    processing_commands.append(("system_readiness", [python, "src/build_system_readiness.py", str(DATABASE)], 90))
    processing_commands.append(
        (
            "accuracy_artifact",
            [
                python,
                "src/build_accuracy_report_artifact.py",
                str(DATABASE),
                "reports/accuracy-readiness/artifact.json",
            ],
            90,
        )
    )
    report_command = portable_report_command()
    if report_command:
        processing_commands.append(("accuracy_report", report_command, 90))

    steps = [run_step(name, command, timeout) for name, command, timeout in setup_commands]
    source_stage_started = time.monotonic()
    for name, command, timeout in source_commands:
        remaining = SOURCE_STAGE_BUDGET_SECONDS - (time.monotonic() - source_stage_started)
        if remaining < 10:
            step = budget_exhausted_step(name)
            route_id, _ = SOURCE_ROUTE_METRICS[name]
            record_source_health(
                DATABASE,
                route_id=route_id,
                cycle_id=started_at,
                status="budget_exhausted",
                duration_seconds=0,
                records_received=0,
                newest_source_time=None,
                freshness_lag_minutes=None,
                error_code="SOURCE_STAGE_BUDGET_EXHAUSTED",
                message=step["output"],
            )
        else:
            step = run_step(
                name,
                command,
                min(timeout, max(10, int(remaining))),
                {"MAEPING_CYCLE_ID": started_at},
            )
            record_step_health(step, started_at)
        steps.append(step)
    source_stage_duration = round(time.monotonic() - source_stage_started, 3)
    steps.extend(
        run_step(name, command, timeout) for name, command, timeout in processing_commands
    )

    # Write the current cycle status before building the self-contained dashboard
    # so the embedded operational badge reflects this same run rather than N-1.
    source_step_names = set(SOURCE_ROUTE_METRICS)
    failed = [
        step["name"]
        for step in steps
        if step["status"] != "success" and step["name"] not in source_step_names
    ]
    source_summary = source_health_summary(started_at) if source_commands else {
        "routes_checked": 0,
        "healthy_routes": 0,
        "degraded_routes": [],
        "records_received": 0,
        "duration_seconds_sum": 0,
    }
    payload = {
        "cycle_started_at": started_at,
        "cycle_finished_at": iso_now(),
        "status": "success" if not failed else "partial_failure",
        "local_only": local_only,
        "failed_steps": failed,
        "degraded_sources": source_summary["degraded_routes"],
        "source_stage_budget_seconds": SOURCE_STAGE_BUDGET_SECONDS,
        "source_stage_duration_seconds": source_stage_duration,
        "source_health": source_summary,
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
        failed = [
            step["name"]
            for step in steps
            if step["status"] != "success" and step["name"] not in source_step_names
        ]
        payload.update(
            {
                "cycle_finished_at": iso_now(),
                "status": "success" if not failed else "partial_failure",
                "failed_steps": failed,
                "degraded_sources": source_summary["degraded_routes"],
                "source_health": source_summary,
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
