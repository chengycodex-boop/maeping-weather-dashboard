# -*- coding: utf-8 -*-
"""Maintain the reviewed source portfolio without rebuilding the database."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from init_db import ROOT
except ModuleNotFoundError:
    from src.init_db import ROOT


SCHEMA = ROOT / "db" / "schema.sql"
SOURCE_REGISTRY = ROOT / "data" / "source_registry.csv"
SOURCE_ROUTES = ROOT / "data" / "source_routes.csv"
DATABASE = ROOT / "data" / "maeping_weather.db"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def ensure_source_portfolio(database: Path) -> dict[str, int]:
    """Create portfolio tables and upsert reviewed registry rows in place."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        sources = csv_rows(SOURCE_REGISTRY)
        connection.executemany(
            """
            INSERT INTO sources (
                source_id, source_name, provider, source_class, variables,
                spatial_grain, temporal_grain, latency, access_mode,
                authority_tier, operational_role, status, url, limitations
            ) VALUES (
                :source_id, :source_name, :provider, :source_class, :variables,
                :spatial_grain, :temporal_grain, :latency, :access_mode,
                :authority_tier, :operational_role, :status, :url, :limitations
            )
            ON CONFLICT(source_id) DO UPDATE SET
                source_name=excluded.source_name,
                provider=excluded.provider,
                source_class=excluded.source_class,
                variables=excluded.variables,
                spatial_grain=excluded.spatial_grain,
                temporal_grain=excluded.temporal_grain,
                latency=excluded.latency,
                access_mode=excluded.access_mode,
                authority_tier=excluded.authority_tier,
                operational_role=excluded.operational_role,
                status=excluded.status,
                url=excluded.url,
                limitations=excluded.limitations
            """,
            sources,
        )
        routes = csv_rows(SOURCE_ROUTES)
        connection.executemany(
            """
            INSERT INTO source_routes (
                route_id, source_id, domain, geographic_scope, priority_order,
                fallback_group, independence_group, expected_freshness_minutes,
                request_timeout_seconds, max_retries, credential_env, connector,
                enabled, status, notes
            ) VALUES (
                :route_id, :source_id, :domain, :geographic_scope, :priority_order,
                :fallback_group, :independence_group, :expected_freshness_minutes,
                :request_timeout_seconds, :max_retries, :credential_env, :connector,
                :enabled, :status, :notes
            )
            ON CONFLICT(route_id) DO UPDATE SET
                source_id=excluded.source_id,
                domain=excluded.domain,
                geographic_scope=excluded.geographic_scope,
                priority_order=excluded.priority_order,
                fallback_group=excluded.fallback_group,
                independence_group=excluded.independence_group,
                expected_freshness_minutes=excluded.expected_freshness_minutes,
                request_timeout_seconds=excluded.request_timeout_seconds,
                max_retries=excluded.max_retries,
                credential_env=excluded.credential_env,
                connector=excluded.connector,
                enabled=excluded.enabled,
                status=excluded.status,
                notes=excluded.notes
            """,
            routes,
        )
        connection.commit()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign-key violations after portfolio refresh: {violations}")
        return {
            "sources": connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "routes": connection.execute("SELECT COUNT(*) FROM source_routes").fetchone()[0],
            "enabled_routes": connection.execute(
                "SELECT COUNT(*) FROM source_routes WHERE enabled=1"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def record_source_health(
    database: Path,
    *,
    route_id: str,
    cycle_id: str,
    status: str,
    duration_seconds: float,
    records_received: int | None,
    newest_source_time: str | None,
    freshness_lag_minutes: float | None,
    error_code: str | None,
    message: str,
) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        source = connection.execute(
            "SELECT source_id FROM source_routes WHERE route_id=?", (route_id,)
        ).fetchone()
        if source is None:
            raise RuntimeError(f"unknown source route: {route_id}")
        now = iso_now()
        connection.execute(
            """
            INSERT INTO source_health_latest (
                route_id, source_id, cycle_id, checked_at, status,
                duration_seconds, records_received, newest_source_time,
                freshness_lag_minutes, error_code, message, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id) DO UPDATE SET
                source_id=excluded.source_id,
                cycle_id=excluded.cycle_id,
                checked_at=excluded.checked_at,
                status=excluded.status,
                duration_seconds=excluded.duration_seconds,
                records_received=excluded.records_received,
                newest_source_time=excluded.newest_source_time,
                freshness_lag_minutes=excluded.freshness_lag_minutes,
                error_code=excluded.error_code,
                message=excluded.message,
                updated_at=excluded.updated_at
            """,
            (
                route_id,
                source[0],
                cycle_id,
                now,
                status,
                round(max(0.0, duration_seconds), 3),
                records_received,
                newest_source_time,
                freshness_lag_minutes,
                error_code,
                message[-2000:] or status,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def output_metric(output: str, name: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(name)}=([^\s]+)", output)
    return match.group(1) if match else None


def replace_hazard_features(database: Path, source_id: str, rows: list[dict]) -> int:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM hazard_features_latest WHERE source_id=?", (source_id,))
        if rows:
            connection.executemany(
                """
                INSERT INTO hazard_features_latest (
                    source_id, feature_id, hazard_type, observed_at, latitude,
                    longitude, geometry_type, geometry_json, value, unit,
                    severity, title, source_url, properties_json, quality_flag,
                    updated_at
                ) VALUES (
                    :source_id, :feature_id, :hazard_type, :observed_at, :latitude,
                    :longitude, :geometry_type, :geometry_json, :value, :unit,
                    :severity, :title, :source_url, :properties_json, :quality_flag,
                    :updated_at
                )
                """,
                rows,
            )
        connection.commit()
        return len(rows)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def feature_row(
    *,
    source_id: str,
    feature_id: str,
    hazard_type: str,
    geometry: dict,
    properties: dict,
    observed_at: str | None,
    value: float | None,
    unit: str | None,
    severity: str | None,
    title: str | None,
    source_url: str | None,
) -> dict:
    latitude = longitude = None
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
    return {
        "source_id": source_id,
        "feature_id": str(feature_id),
        "hazard_type": hazard_type,
        "observed_at": observed_at,
        "latitude": latitude,
        "longitude": longitude,
        "geometry_type": str(geometry.get("type") or "Unknown"),
        "geometry_json": json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
        "value": value,
        "unit": unit,
        "severity": severity,
        "title": title,
        "source_url": source_url,
        "properties_json": json.dumps(properties, ensure_ascii=False, separators=(",", ":"), default=str),
        "quality_flag": "provisional",
        "updated_at": iso_now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=DATABASE)
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")
    counts = ensure_source_portfolio(args.database)
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
