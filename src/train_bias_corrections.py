# -*- coding: utf-8 -*-
"""Train guarded bias corrections only after enough real forecast pairs exist."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from evaluate_forecasts import LEAD_BUCKETS, MIN_READY_DAYS, MODEL_NAME, SOURCE_ID, lead_bucket, readiness_status
    from init_db import ROOT, SCHEMA
    from station_capabilities import verification_variables
except ModuleNotFoundError:
    from src.evaluate_forecasts import LEAD_BUCKETS, MIN_READY_DAYS, MODEL_NAME, SOURCE_ID, lead_bucket, readiness_status
    from src.init_db import ROOT, SCHEMA
    from src.station_capabilities import verification_variables


OUTPUT = ROOT / "data" / "calibration_latest.json"
MIN_RAIN_EVENTS = 10


def calibration_parameter(variable: str, observed: list[float], forecast: list[float]) -> tuple[str, float | None, int]:
    events = sum(value >= 0.1 for value in observed)
    if not observed:
        return ("multiplicative_ratio" if variable == "precipitation" else "additive_offset", None, events)
    if variable == "temperature":
        return "additive_offset", sum(o - f for o, f in zip(observed, forecast)) / len(observed), events
    forecast_total = sum(forecast)
    if forecast_total <= 0:
        return "multiplicative_ratio", None, events
    return "multiplicative_ratio", sum(observed) / forecast_total, events


def train(database: Path) -> dict:
    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        sensors = [dict(row) for row in connection.execute(
            """SELECT location_id, code, verification_status FROM locations
               WHERE verification_status LIKE 'priority_1%' OR verification_status LIKE 'priority_2%'
               ORDER BY code"""
        )]
        groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in connection.execute(
            """SELECT f.location_id, f.variable, f.valid_at, f.lead_hours,
                      f.value AS forecast_value, o.value AS observed_value
               FROM forecasts f JOIN observations o
                 ON o.location_id=f.location_id AND o.variable=f.variable AND o.observed_at=f.valid_at
                AND (f.variable='temperature' OR (f.period_minutes=60 AND o.period_minutes=60))
               WHERE f.source_id=? AND f.model_name=?
                 AND f.variable IN ('precipitation','temperature')
                 AND o.quality_flag NOT IN ('suspect','missing')""",
            (SOURCE_ID, MODEL_NAME),
        ):
            bucket = lead_bucket(float(row["lead_hours"]))
            if bucket:
                groups[(row["location_id"], row["variable"], bucket)].append(dict(row))
        results = []
        connection.execute("DELETE FROM calibration_models_latest")
        for sensor in sensors:
            for variable in verification_variables(sensor["code"], sensor["verification_status"]):
                for bucket in LEAD_BUCKETS:
                    rows = groups.get((sensor["location_id"], variable, bucket), [])
                    observed = [row["observed_value"] for row in rows]
                    forecast = [row["forecast_value"] for row in rows]
                    method, parameter, events = calibration_parameter(variable, observed, forecast)
                    days = len({row["valid_at"][:10] for row in rows})
                    status = readiness_status(len(rows), days)
                    if variable == "precipitation" and status == "ready" and events < MIN_RAIN_EVENTS:
                        status = "provisional"
                    if parameter is None and status == "ready":
                        status = "provisional"
                    active_parameter = parameter if status == "ready" else None
                    item = {
                        "forecast_source_id": SOURCE_ID, "model_name": MODEL_NAME,
                        "location_id": sensor["location_id"], "code": sensor["code"],
                        "variable": variable, "lead_bucket": bucket, "trained_at": trained_at,
                        "window_start": min((row["valid_at"] for row in rows), default=None),
                        "window_end": max((row["valid_at"] for row in rows), default=None),
                        "pair_count": len(rows), "sample_days": days, "event_count": events,
                        "method": method, "parameter_value": active_parameter,
                        "readiness_status": status,
                        "methodology_note": (
                            "Activation requires >=60 sample days; precipitation also requires >=10 observed rain events. "
                            "Temperature uses mean additive offset; precipitation uses observed/forecast total ratio. "
                            "No parameter is exposed before the gate passes."
                        ),
                    }
                    results.append(item)
                    connection.execute(
                        """INSERT INTO calibration_models_latest (
                               forecast_source_id, model_name, location_id, variable, lead_bucket,
                               trained_at, window_start, window_end, pair_count, sample_days,
                               event_count, method, parameter_value, readiness_status, methodology_note
                           ) VALUES (:forecast_source_id,:model_name,:location_id,:variable,:lead_bucket,
                               :trained_at,:window_start,:window_end,:pair_count,:sample_days,
                               :event_count,:method,:parameter_value,:readiness_status,:methodology_note)""",
                        item,
                    )
        connection.commit()
    finally:
        connection.close()
    return {"trained_at": trained_at, "minimum_ready_days": MIN_READY_DAYS,
            "minimum_rain_events": MIN_RAIN_EVENTS, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data/maeping_weather.db")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = train(args.database)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = sum(row["readiness_status"] == "ready" for row in payload["results"])
    print(f"calibration_groups={len(payload['results'])} ready={ready} guarded={len(payload['results'])-ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
