# -*- coding: utf-8 -*-
"""Fetch recent earthquakes around Thailand from the USGS GeoJSON service."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from init_db import ROOT
    from source_portfolio import (
        ensure_source_portfolio,
        feature_row,
        record_source_health,
        replace_hazard_features,
    )
except ModuleNotFoundError:
    from src.init_db import ROOT
    from src.source_portfolio import (
        ensure_source_portfolio,
        feature_row,
        record_source_health,
        replace_hazard_features,
    )


DATABASE = ROOT / "data" / "maeping_weather.db"
SOURCE_ID = "usgs_earthquake_geojson"
ROUTE_ID = "usgs_earthquakes"
BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def epoch_iso(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def fetch_geojson(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/geo+json", "User-Agent": "maeping-environment-hub/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def convert(payload: dict, endpoint: str) -> tuple[list[dict], str | None]:
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("USGS response does not contain features")
    rows: list[dict] = []
    newest: str | None = None
    for feature in features:
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        observed_at = epoch_iso(properties.get("time"))
        newest = max(newest, observed_at) if newest and observed_at else (observed_at or newest)
        magnitude = properties.get("mag")
        try:
            magnitude = float(magnitude) if magnitude is not None else None
        except (TypeError, ValueError):
            magnitude = None
        alert = properties.get("alert")
        if alert is None and magnitude is not None:
            alert = "high" if magnitude >= 6 else "moderate" if magnitude >= 4.5 else "information"
        rows.append(
            feature_row(
                source_id=SOURCE_ID,
                feature_id=str(feature.get("id") or properties.get("code")),
                hazard_type="earthquake",
                geometry=geometry,
                properties=properties,
                observed_at=observed_at,
                value=magnitude,
                unit="magnitude" if magnitude is not None else None,
                severity=str(alert) if alert is not None else None,
                title=str(properties.get("title") or properties.get("place") or "Earthquake"),
                source_url=str(properties.get("url") or endpoint),
            )
        )
    return rows, newest


def run(database: Path) -> int:
    ensure_source_portfolio(database)
    cycle_id = os.environ.get("MAEPING_CYCLE_ID", "").strip() or datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    start = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
    parameters = {
        "format": "geojson",
        "starttime": start,
        "minlatitude": 5,
        "maxlatitude": 30,
        "minlongitude": 85,
        "maxlongitude": 110,
        "orderby": "time",
        "limit": 2000,
    }
    endpoint = f"{BASE_URL}?{urllib.parse.urlencode(parameters)}"
    try:
        payload = fetch_geojson(endpoint)
        rows, newest = convert(payload, endpoint)
        count = replace_hazard_features(database, SOURCE_ID, rows)
        lag = None
        if newest:
            lag = max(
                0.0,
                (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds() / 60,
            )
        status = "no_data" if count == 0 else "success"
        record_source_health(
            database,
            route_id=ROUTE_ID,
            cycle_id=cycle_id,
            status=status,
            duration_seconds=time.monotonic() - started,
            records_received=count,
            newest_source_time=newest,
            freshness_lag_minutes=lag,
            error_code=None,
            message="USGS events from the last 7 days for Thailand and neighbouring seismic zones",
        )
        print(
            f"usgs_earthquakes status={status} records={count} "
            f"newest_source_time={newest or 'unknown'}"
        )
        return 0
    except Exception as error:
        record_source_health(
            database,
            route_id=ROUTE_ID,
            cycle_id=cycle_id,
            status="failed",
            duration_seconds=time.monotonic() - started,
            records_received=0,
            newest_source_time=None,
            freshness_lag_minutes=None,
            error_code=type(error).__name__,
            message=str(error),
        )
        print(f"usgs_earthquakes status=failed records=0 error={type(error).__name__}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=DATABASE)
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"database not found: {args.database}")
    return run(args.database)


if __name__ == "__main__":
    raise SystemExit(main())
