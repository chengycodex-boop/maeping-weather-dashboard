# -*- coding: utf-8 -*-
"""Pair forecasts with same-location observations and compute guarded metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT, SCHEMA
    from metrics import verification_summary
    from station_capabilities import verification_variables
except ModuleNotFoundError:  # Imported as src.evaluate_forecasts in tests.
    from src.init_db import ROOT, SCHEMA
    from src.metrics import verification_summary
    from src.station_capabilities import verification_variables


SOURCE_ID = "open_meteo_best_match"
MODEL_NAME = "Open-Meteo Best Match"
LEAD_BUCKETS = ("0–6", "7–12", "13–24", "25–48", "49–72", "73–120", "121–168")
MIN_READY_DAYS = 60


def lead_bucket(hours: float) -> str | None:
    if 0 <= hours <= 6:
        return "0–6"
    if hours <= 12:
        return "7–12"
    if hours <= 24:
        return "13–24"
    if hours <= 48:
        return "25–48"
    if hours <= 72:
        return "49–72"
    if hours <= 120:
        return "73–120"
    if hours <= 168:
        return "121–168"
    return None


def readiness_status(pair_count: int, sample_days: int) -> str:
    if pair_count == 0:
        return "no_pairs"
    if sample_days < 30:
        return "accumulating"
    if sample_days < MIN_READY_DAYS:
        return "provisional"
    return "ready"


def _result_id(computed_at: str, location_id: str, variable: str, bucket: str) -> str:
    raw = "|".join((SOURCE_ID, MODEL_NAME, location_id, variable, bucket, computed_at))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evaluate(database: Path) -> dict:
    computed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        sensor_rows = connection.execute(
            """
            SELECT location_id, code, name_th, verification_status
            FROM locations
            WHERE location_id LIKE 'THAIWATER_%'
              AND (
                    verification_status LIKE 'priority_1%'
                    OR verification_status LIKE 'priority_2%'
                  )
            ORDER BY code
            """
        ).fetchall()
        sensors = {row["location_id"]: dict(row) for row in sensor_rows}

        pairs: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        query_rows = connection.execute(
            """
            SELECT f.location_id, f.variable, f.valid_at, f.lead_hours,
                   f.value AS forecast_value, o.value AS observed_value,
                   o.quality_flag, f.model_run
            FROM forecasts f
            JOIN observations o
              ON o.location_id = f.location_id
             AND o.variable = f.variable
             AND o.observed_at = f.valid_at
             AND (
                    f.variable = 'temperature'
                    OR (f.variable = 'precipitation'
                        AND f.period_minutes = 60
                        AND o.period_minutes = 60)
                 )
            WHERE f.source_id = ?
              AND f.model_name = ?
              AND f.variable IN ('precipitation', 'temperature')
              AND o.quality_flag NOT IN ('suspect', 'missing')
            ORDER BY f.valid_at, f.model_run
            """,
            (SOURCE_ID, MODEL_NAME),
        ).fetchall()
        for row in query_rows:
            if row["location_id"] not in sensors:
                continue
            bucket = lead_bucket(float(row["lead_hours"]))
            if bucket is not None:
                pairs[(row["location_id"], row["variable"], bucket)].append(dict(row))

        results = []
        methodology = (
            "จับคู่ location_id+variable+valid_at เดียวกัน; ฝนใช้เฉพาะรอบสะสม 60 นาที; "
            "อุณหภูมิเทียบค่าที่ timestamp เดียวกัน; ตัด suspect/missing; "
            "ThaiWater timestamp ยังถือเป็น Asia/Bangkok แบบ provisional; ready เมื่อมีอย่างน้อย 60 วัน"
        )
        for location_id, sensor in sensors.items():
            variables = verification_variables(
                sensor["code"], sensor["verification_status"]
            )
            for variable in variables:
                for bucket in LEAD_BUCKETS:
                    group = pairs.get((location_id, variable, bucket), [])
                    actual = [row["observed_value"] for row in group]
                    forecast = [row["forecast_value"] for row in group]
                    days = len({row["valid_at"][:10] for row in group})
                    threshold = 0.1 if variable == "precipitation" else None
                    summary = (
                        verification_summary(actual, forecast, event_threshold=threshold)
                        if group
                        else {"n": 0}
                    )
                    result = {
                        "result_id": _result_id(computed_at, location_id, variable, bucket),
                        "computed_at": computed_at,
                        "forecast_source_id": SOURCE_ID,
                        "model_name": MODEL_NAME,
                        "location_id": location_id,
                        "code": sensor["code"],
                        "name_th": sensor["name_th"],
                        "variable": variable,
                        "lead_bucket": bucket,
                        "window_start": min((row["valid_at"] for row in group), default=None),
                        "window_end": max((row["valid_at"] for row in group), default=None),
                        "pair_count": len(group),
                        "sample_days": days,
                        "mae": summary.get("mae"),
                        "rmse": summary.get("rmse"),
                        "mean_bias": summary.get("mean_bias"),
                        "percent_bias": summary.get("percent_bias"),
                        "wape": summary.get("wape"),
                        "event_threshold": threshold,
                        "hits": summary.get("hits"),
                        "misses": summary.get("misses"),
                        "false_alarms": summary.get("false_alarms"),
                        "correct_negatives": summary.get("correct_negatives"),
                        "pod": summary.get("pod"),
                        "far": summary.get("far"),
                        "csi": summary.get("csi"),
                        "readiness_status": readiness_status(len(group), days),
                        "methodology_note": methodology,
                    }
                    results.append(result)
                    connection.execute(
                        """
                        INSERT INTO verification_results (
                            result_id, computed_at, forecast_source_id, model_name,
                            location_id, variable, lead_bucket, window_start, window_end,
                            pair_count, sample_days, mae, rmse, mean_bias, percent_bias,
                            wape, event_threshold, hits, misses, false_alarms,
                            correct_negatives, pod, far, csi, readiness_status,
                            methodology_note
                        ) VALUES (
                            :result_id, :computed_at, :forecast_source_id, :model_name,
                            :location_id, :variable, :lead_bucket, :window_start, :window_end,
                            :pair_count, :sample_days, :mae, :rmse, :mean_bias, :percent_bias,
                            :wape, :event_threshold, :hits, :misses, :false_alarms,
                            :correct_negatives, :pod, :far, :csi, :readiness_status,
                            :methodology_note
                        )
                        """,
                        result,
                    )
        connection.commit()
    finally:
        connection.close()

    return {
        "computed_at": computed_at,
        "minimum_ready_days": MIN_READY_DAYS,
        "timezone_assumption": "Asia/Bangkok (+07:00), provisional",
        "pairing_method": methodology,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "accuracy_latest.json"
    )
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")
    payload = evaluate(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    counts: dict[str, int] = defaultdict(int)
    for result in payload["results"]:
        counts[result["readiness_status"]] += 1
    paired = sum(result["pair_count"] for result in payload["results"])
    print(
        f"verification_groups={len(payload['results'])} matched_pairs={paired} "
        + " ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
