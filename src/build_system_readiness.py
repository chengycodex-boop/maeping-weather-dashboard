# -*- coding: utf-8 -*-
"""Build transparent technical/evidence readiness and provisional signals."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


OUTPUT = ROOT / "data" / "system_readiness_latest.json"
THRESHOLDS = ROOT / "config" / "operational_thresholds.json"
TMD_AUDIT = ROOT / "data" / "tmd_qpe_source_audit.json"
BOUNDARY_AUDIT = ROOT / "data" / "boundary_comparison_latest.json"


def weighted_score(components: list[dict]) -> float:
    total_weight = sum(item["weight"] for item in components)
    return round(sum(item["score"] * item["weight"] for item in components) / total_weight, 1)


def threshold_level(value: float, thresholds: dict) -> str:
    if value >= thresholds["critical"]:
        return "critical"
    if value >= thresholds["warning"]:
        return "warning"
    if value >= thresholds["watch"]:
        return "watch"
    return "normal"


def build(database: Path, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    tmd = json.loads(TMD_AUDIT.read_text(encoding="utf-8")) if TMD_AUDIT.exists() else None
    boundary = json.loads(BOUNDARY_AUDIT.read_text(encoding="utf-8")) if BOUNDARY_AUDIT.exists() else None
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        observations = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        operational_locations = connection.execute(
            """SELECT COUNT(*) FROM locations WHERE verification_status LIKE 'priority_1%'
               OR verification_status LIKE 'priority_2%'"""
        ).fetchone()[0]
        locations_with_observations = connection.execute(
            """SELECT COUNT(DISTINCT o.location_id) FROM observations o JOIN locations l USING(location_id)
               WHERE l.verification_status LIKE 'priority_1%' OR l.verification_status LIKE 'priority_2%'"""
        ).fetchone()[0]
        forecast_history = connection.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        grid_cells = connection.execute("SELECT COUNT(*) FROM grid_cells").fetchone()[0]
        grid_forecasts = connection.execute("SELECT COUNT(*) FROM grid_forecasts_latest").fetchone()[0]
        grid_estimates = connection.execute("SELECT COUNT(*) FROM grid_estimates_latest").fetchone()[0]
        latest_verification = connection.execute("SELECT MAX(computed_at) FROM verification_results").fetchone()[0]
        verification = [] if not latest_verification else [dict(row) for row in connection.execute(
            """SELECT readiness_status, pair_count, sample_days FROM verification_results
               WHERE computed_at=?""", (latest_verification,)
        )]
        forecast_rows = [dict(row) for row in connection.execute(
            """SELECT grid_id, valid_at, variable, value FROM grid_forecasts_latest
               WHERE variable IN ('precipitation','temperature','precipitation_probability')"""
        )]
        observed_rows = [dict(row) for row in connection.execute(
            """SELECT location_id, variable, observed_at, period_minutes, value FROM observations
               WHERE variable IN ('precipitation','temperature') ORDER BY observed_at"""
        )]
    finally:
        connection.close()

    ready_groups = sum(row["readiness_status"] == "ready" for row in verification)
    max_sample_days = max((row["sample_days"] for row in verification), default=0)
    technical = [
        {"component": "data_model", "weight": 10, "score": 100},
        {"component": "ground_observations", "weight": 10, "score": min(100, observations / 500 * 100)},
        {"component": "point_forecast_history", "weight": 10, "score": 100 if forecast_history else 0},
        {"component": "grid_forecast", "weight": 10, "score": 100 if grid_cells >= 35 and grid_forecasts else 0},
        {"component": "dashboard", "weight": 10, "score": 100 if (ROOT / "dashboard/index.html").exists() else 0},
        {"component": "automation", "weight": 10, "score": 100 if (ROOT / "src/run_operational_cycle.py").exists() else 0},
        {"component": "quality_gates", "weight": 10, "score": 100 if (ROOT / "data/quality_latest.json").exists() else 0},
        {"component": "radar_qpe_connector", "weight": 10, "score": 100 if (ROOT / "src/fetch_tmd_qpe.py").exists() else 0},
        {"component": "satellite_connector", "weight": 5, "score": 20},
        {"component": "official_boundary", "weight": 5, "score": 50 if boundary else 0},
        {"component": "accuracy_engine", "weight": 5, "score": 100 if verification else 0},
        {"component": "bias_correction_pipeline", "weight": 5,
         "score": 100 if (ROOT / "src/train_bias_corrections.py").exists() else 0},
    ]
    evidence = [
        {"component": "operational_station_coverage", "weight": 20,
         "score": 100 * locations_with_observations / max(1, operational_locations)},
        {"component": "forecast_observation_history", "weight": 30,
         "score": min(100, 100 * max_sample_days / 60)},
        {"component": "ready_verification_groups", "weight": 20,
         "score": 100 * ready_groups / max(1, len(verification))},
        {"component": "fresh_spatial_ground_truth", "weight": 15,
         "score": 100 if tmd and tmd.get("status") == "fresh" and grid_estimates else 0},
        {"component": "boundary_authority", "weight": 5, "score": 50 if boundary else 0},
        {"component": "forecast_spatial_coverage", "weight": 10, "score": 100 if grid_forecasts else 0},
    ]

    cutoff = now.timestamp()
    next24 = [row for row in forecast_rows if cutoff <= datetime.fromisoformat(row["valid_at"]).timestamp() <= cutoff + 86400]
    forecast_rain = [row["value"] for row in next24 if row["variable"] == "precipitation"]
    forecast_temp = [row["value"] for row in next24 if row["variable"] == "temperature"]
    forecast_probability = [row["value"] for row in next24 if row["variable"] == "precipitation_probability"]
    observed_rain_by_location: dict[str, list[dict]] = {}
    latest_temps: dict[str, dict] = {}
    for row in observed_rows:
        if row["variable"] == "precipitation" and row["period_minutes"] == 60:
            observed_rain_by_location.setdefault(row["location_id"], []).append(row)
        elif row["variable"] == "temperature":
            if row["location_id"] not in latest_temps or row["observed_at"] > latest_temps[row["location_id"]]["observed_at"]:
                latest_temps[row["location_id"]] = row
    rain24_values = []
    for rows in observed_rain_by_location.values():
        end = max(datetime.fromisoformat(row["observed_at"]).timestamp() for row in rows)
        rain24_values.append(sum(row["value"] for row in rows if end - 86400 < datetime.fromisoformat(row["observed_at"]).timestamp() <= end))
    metrics = {
        "forecast_max_hourly_rain_next_24h_mm": max(forecast_rain, default=None),
        "forecast_max_temperature_next_24h_c": max(forecast_temp, default=None),
        "forecast_max_rain_probability_next_24h_percent": max(forecast_probability, default=None),
        "observed_max_station_rain_24h_mm": max(rain24_values, default=None),
        "observed_max_temperature_c": max((row["value"] for row in latest_temps.values()), default=None),
    }
    signals = []
    for metric, threshold_key in [
        ("forecast_max_hourly_rain_next_24h_mm", "rain_hourly_mm"),
        ("observed_max_station_rain_24h_mm", "rain_observed_24h_mm"),
        ("forecast_max_temperature_next_24h_c", "temperature_c"),
    ]:
        value = metrics[metric]
        if value is not None:
            signals.append({"metric": metric, "value": round(value, 2), "level": threshold_level(value, thresholds[threshold_key])})
    if tmd and tmd.get("status") != "fresh":
        signals.append({"metric": "tmd_qpe_freshness", "value": tmd.get("age_hours"), "level": "data_gap"})
    if not ready_groups:
        signals.append({"metric": "accuracy_evidence", "value": max_sample_days, "level": "accumulating"})

    technical_score = weighted_score(technical)
    evidence_score = weighted_score(evidence)
    return {
        "computed_at": now.isoformat(),
        "completion": {
            "technical_percent": technical_score,
            "evidence_percent": evidence_score,
            "operational_readiness_percent": round(technical_score * 0.6 + evidence_score * 0.4, 1),
            "method": "60% technical implementation + 40% evidence maturity",
        },
        "technical_components": technical,
        "evidence_components": evidence,
        "metrics": metrics,
        "signals": signals,
        "thresholds": thresholds,
        "counts": {"observations": observations, "forecast_history": forecast_history,
                   "grid_cells": grid_cells, "grid_forecasts_latest": grid_forecasts,
                   "grid_estimates_latest": grid_estimates, "verification_groups": len(verification),
                   "ready_verification_groups": ready_groups, "max_sample_days": max_sample_days},
        "remaining_external_dependencies": [
            "current official DNP boundary geometry",
            "fresh TMD QPE product or alternative numerical satellite feed",
            "60–90 days of real forecast-observation pairs",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data/maeping_weather.db")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = build(args.database)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    completion = payload["completion"]
    print(f"technical={completion['technical_percent']} evidence={completion['evidence_percent']} operational={completion['operational_readiness_percent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
