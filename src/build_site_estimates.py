# -*- coding: utf-8 -*-
"""Build transparent three-hour site estimates from available weather evidence.

The product deliberately distinguishes measurements from estimates.  Reporting
sites with coordinates blend nearby gauges with the latest model value.  Sites
without reviewed coordinates receive a park-wide fallback with wider
uncertainty, never a fabricated point measurement.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


METHOD_VERSION = "maeping_blend_v1"
VARIABLES = {
    "precipitation": {"unit": "mm", "period_minutes": 180, "floor": 0.5},
    "temperature": {"unit": "°C", "period_minutes": 60, "floor": 1.5},
}


@dataclass(frozen=True)
class Component:
    value: float
    weight: float
    label: str


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS site_estimates_latest (
            location_id TEXT NOT NULL REFERENCES locations(location_id),
            variable TEXT NOT NULL CHECK (variable IN ('precipitation', 'temperature')),
            estimate_at TEXT NOT NULL,
            period_minutes INTEGER NOT NULL CHECK (period_minutes > 0),
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            estimate_type TEXT NOT NULL CHECK (
                estimate_type IN ('sensor', 'blended', 'model_only', 'regional_fallback')
            ),
            spatial_basis TEXT NOT NULL CHECK (
                spatial_basis IN ('exact_point', 'area_anchor', 'park_regional')
            ),
            ground_value REAL,
            model_value REAL,
            radar_satellite_value REAL,
            source_count INTEGER NOT NULL CHECK (source_count >= 1),
            source_summary TEXT NOT NULL,
            confidence_score REAL NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
            confidence_level TEXT NOT NULL CHECK (confidence_level IN ('high', 'medium', 'low')),
            uncertainty_low REAL NOT NULL,
            uncertainty_high REAL NOT NULL,
            historical_error_percent REAL,
            validation_status TEXT NOT NULL CHECK (
                validation_status IN ('awaiting_validation', 'provisional', 'validated')
            ),
            method_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (location_id, variable)
        );
        CREATE INDEX IF NOT EXISTS idx_site_estimates_latest_time
            ON site_estimates_latest (variable, estimate_at);
        """
    )


def reference_time(connection: sqlite3.Connection) -> datetime:
    values = [
        row[0]
        for row in connection.execute(
            """
            SELECT MAX(observed_at) FROM observations
            UNION ALL SELECT MAX(issued_at) FROM forecasts
            UNION ALL SELECT MAX(observed_at) FROM grid_estimates_latest
            """
        )
        if row[0]
    ]
    return max((parse_time(value) for value in values), default=datetime.now(timezone.utc))


def reporting_sites(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT location_id, name_th, latitude, longitude, coordinate_role
            FROM locations
            WHERE location_id NOT LIKE 'THAIWATER_%'
              AND coordinate_role <> 'grid_centroid'
            ORDER BY location_id
            """
        )
    ]


def sensor_values(connection: sqlite3.Connection, variable: str, at: datetime) -> list[dict]:
    connection.row_factory = sqlite3.Row
    locations = {
        row["location_id"]: dict(row)
        for row in connection.execute(
            """
            SELECT location_id, latitude, longitude
            FROM locations
            WHERE location_id LIKE 'THAIWATER_%'
              AND latitude IS NOT NULL AND longitude IS NOT NULL
            """
        )
    }
    rows: list[dict] = []
    start = at - timedelta(hours=3 if variable == "precipitation" else 6)
    for location_id, location in locations.items():
        if variable == "precipitation":
            values = connection.execute(
                """
                SELECT observed_at, value FROM observations
                WHERE location_id=? AND variable='precipitation' AND period_minutes=60
                  AND julianday(observed_at)>julianday(?)
                  AND julianday(observed_at)<=julianday(?)
                ORDER BY observed_at
                """,
                (location_id, start.isoformat(), at.isoformat()),
            ).fetchall()
            if not values:
                continue
            value = sum(float(row["value"]) for row in values)
            observed_at = max(row["observed_at"] for row in values)
        else:
            value_row = connection.execute(
                """
                SELECT observed_at, value FROM observations
                WHERE location_id=? AND variable='temperature'
                  AND julianday(observed_at)<=julianday(?)
                ORDER BY julianday(observed_at) DESC LIMIT 1
                """,
                (location_id, at.isoformat()),
            ).fetchone()
            if not value_row or parse_time(value_row["observed_at"]) < start:
                continue
            value = float(value_row["value"])
            observed_at = value_row["observed_at"]
        rows.append({**location, "value": value, "observed_at": observed_at})
    return rows


def interpolated_ground(sensors: list[dict], site: dict) -> tuple[float | None, float | None, int]:
    if site["latitude"] is None or site["longitude"] is None or not sensors:
        return None, None, 0
    ranked = []
    for sensor in sensors:
        distance = haversine_km(
            float(site["latitude"]), float(site["longitude"]),
            float(sensor["latitude"]), float(sensor["longitude"]),
        )
        ranked.append((distance, sensor))
    ranked.sort(key=lambda item: item[0])
    nearest = ranked[:4]
    weights = [1.0 / (distance + 1.0) ** 2 for distance, _ in nearest]
    total = sum(weights)
    value = sum(weight * float(sensor["value"]) for weight, (_, sensor) in zip(weights, nearest)) / total
    return value, nearest[0][0], len(nearest)


def closest_model_value(
    connection: sqlite3.Connection, location_id: str, variable: str, at: datetime
) -> float | None:
    row = connection.execute(
        """
        SELECT value FROM forecasts
        WHERE model_run=(SELECT MAX(model_run) FROM forecasts)
          AND location_id=? AND variable=?
        ORDER BY ABS(julianday(valid_at)-julianday(?))
        LIMIT 1
        """,
        (location_id, variable, at.isoformat()),
    ).fetchone()
    return None if row is None else float(row[0])


def regional_model_values(connection: sqlite3.Connection, variable: str, at: datetime) -> list[float]:
    return [
        float(row[0])
        for row in connection.execute(
            """
            SELECT f.value
            FROM forecasts f JOIN locations l USING(location_id)
            WHERE f.model_run=(SELECT MAX(model_run) FROM forecasts)
              AND f.variable=? AND l.location_id NOT LIKE 'THAIWATER_%'
              AND l.coordinate_role <> 'grid_centroid'
              AND ABS(julianday(f.valid_at)-julianday(?)) <= 0.126
            """,
            (variable, at.isoformat()),
        )
    ]


def radar_value(connection: sqlite3.Connection, site: dict) -> float | None:
    if site["latitude"] is None or site["longitude"] is None:
        return None
    row = connection.execute(
        """
        SELECT e.value
        FROM grid_estimates_latest e
        JOIN locations l ON l.location_id=e.grid_id
        ORDER BY ((l.latitude-?)*(l.latitude-?) + (l.longitude-?)*(l.longitude-?))
        LIMIT 1
        """,
        (site["latitude"], site["latitude"], site["longitude"], site["longitude"]),
    ).fetchone()
    return None if row is None else float(row[0])


def confidence(site: dict, components: list[Component], nearest_km: float | None) -> float:
    role_score = {"exact_station": 72.0, "area_anchor": 58.0, "unknown": 30.0}.get(
        site["coordinate_role"], 30.0
    )
    diversity = min(15.0, max(0, len(components) - 1) * 7.5)
    proximity = 0.0 if nearest_km is None else max(0.0, 13.0 - min(nearest_km, 65.0) / 5.0)
    return round(min(86.0, role_score + diversity + proximity), 1)


def build_estimates(database: Path) -> list[dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        at = reference_time(connection)
        sites = reporting_sites(connection)
        output: list[dict] = []
        for variable, config in VARIABLES.items():
            sensors = sensor_values(connection, variable, at)
            regional_ground = median([row["value"] for row in sensors]) if sensors else None
            model_values = regional_model_values(connection, variable, at)
            regional_model = median(model_values) if model_values else None
            for site in sites:
                ground, nearest_km, gauge_count = interpolated_ground(sensors, site)
                model = closest_model_value(connection, site["location_id"], variable, at)
                radar = radar_value(connection, site) if variable == "precipitation" else None
                located = site["latitude"] is not None and site["longitude"] is not None
                components: list[Component] = []
                if ground is not None:
                    ground_weight = 0.72 if nearest_km is not None and nearest_km <= 1 else 0.48
                    components.append(Component(ground, ground_weight, f"สถานีภาคพื้นดิน {gauge_count} จุด"))
                if radar is not None:
                    components.append(Component(radar, 0.27, "เรดาร์/ดาวเทียมเชิงพื้นที่"))
                if model is not None:
                    components.append(Component(model, 0.34, "แบบจำลองพยากรณ์"))
                if not components:
                    if regional_ground is not None:
                        components.append(Component(float(regional_ground), 0.45, "ค่ากลางสถานีรอบอุทยาน"))
                    if regional_model is not None:
                        components.append(Component(float(regional_model), 0.35, "ค่ากลางแบบจำลองพื้นที่"))
                if not components:
                    continue
                weight_total = sum(component.weight for component in components)
                value = sum(component.value * component.weight for component in components) / weight_total
                if variable == "precipitation":
                    value = max(0.0, value)
                score = confidence(site, components, nearest_km)
                if not located:
                    score = min(score, 42.0)
                level = "high" if score >= 75 else "medium" if score >= 55 else "low"
                spread = max(component.value for component in components) - min(
                    component.value for component in components
                )
                relative_floor = abs(value) * 0.3 if variable == "precipitation" else 0.0
                uncertainty = max(config["floor"], spread * 0.75, relative_floor)
                if not located:
                    uncertainty = max(uncertainty, 2.0 if variable == "precipitation" else 3.0)
                estimate_type = (
                    "regional_fallback" if not located
                    else "blended" if len(components) > 1
                    else "model_only" if components[0].label == "แบบจำลองพยากรณ์"
                    else "sensor"
                )
                spatial_basis = (
                    "park_regional" if not located
                    else "exact_point" if site["coordinate_role"] == "exact_station"
                    else "area_anchor"
                )
                output.append(
                    {
                        "location_id": site["location_id"],
                        "variable": variable,
                        "estimate_at": at.isoformat(),
                        "period_minutes": config["period_minutes"],
                        "value": round(value, 3),
                        "unit": config["unit"],
                        "estimate_type": estimate_type,
                        "spatial_basis": spatial_basis,
                        "ground_value": None if ground is None else round(ground, 3),
                        "model_value": None if model is None else round(model, 3),
                        "radar_satellite_value": None if radar is None else round(radar, 3),
                        "source_count": len(components),
                        "source_summary": " + ".join(component.label for component in components),
                        "confidence_score": score,
                        "confidence_level": level,
                        "uncertainty_low": round(max(0.0, value - uncertainty) if variable == "precipitation" else value - uncertainty, 3),
                        "uncertainty_high": round(value + uncertainty, 3),
                        "historical_error_percent": None,
                        "validation_status": "awaiting_validation",
                        "method_version": METHOD_VERSION,
                        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    }
                )
        connection.execute("DELETE FROM site_estimates_latest")
        connection.executemany(
            """
            INSERT INTO site_estimates_latest (
                location_id, variable, estimate_at, period_minutes, value, unit,
                estimate_type, spatial_basis, ground_value, model_value,
                radar_satellite_value, source_count, source_summary,
                confidence_score, confidence_level, uncertainty_low,
                uncertainty_high, historical_error_percent, validation_status,
                method_version, updated_at
            ) VALUES (
                :location_id, :variable, :estimate_at, :period_minutes, :value, :unit,
                :estimate_type, :spatial_basis, :ground_value, :model_value,
                :radar_satellite_value, :source_count, :source_summary,
                :confidence_score, :confidence_level, :uncertainty_low,
                :uncertainty_high, :historical_error_percent, :validation_status,
                :method_version, :updated_at
            )
            """,
            output,
        )
        connection.commit()
        return output
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    args = parser.parse_args()
    rows = build_estimates(args.database)
    reporting_sites_count = len({row["location_id"] for row in rows})
    low_confidence = sum(row["confidence_level"] == "low" for row in rows)
    print(
        f"site_estimates={len(rows)} reporting_sites={reporting_sites_count} "
        f"low_confidence_rows={low_confidence} method={METHOD_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
