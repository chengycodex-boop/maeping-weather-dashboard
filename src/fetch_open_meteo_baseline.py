"""Fetch a seven-day forecast baseline at reporting and sensor locations.

Open-Meteo Best Match is an aggregator and is deliberately labelled as a
secondary baseline. It is not treated as a ground observation or the final
multi-model product.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from init_db import ROOT, build_database
except ModuleNotFoundError:  # Imported as src.fetch_open_meteo_baseline in tests.
    from src.init_db import ROOT, build_database


STATIONS = ROOT / "data" / "stations.csv"
SUPPORT_STATIONS = ROOT / "data" / "support_station_shortlist.csv"
SOURCE_ID = "open_meteo_best_match"
MODEL_NAME = "Open-Meteo Best Match"
ENDPOINT = "https://api.open-meteo.com/v1/forecast"
VARIABLES = {
    "precipitation": ("precipitation", "mm"),
    "precipitation_probability": ("precipitation_probability", "%"),
    "temperature_2m": ("temperature", "°C"),
    "apparent_temperature": ("apparent_temperature", "°C"),
    "relative_humidity_2m": ("relative_humidity", "%"),
    "dew_point_2m": ("dew_point", "°C"),
}


def load_located_stations() -> list[dict[str, str]]:
    with STATIONS.open(encoding="utf-8-sig", newline="") as handle:
        reporting = [
            row for row in csv.DictReader(handle) if row["latitude"] and row["longitude"]
        ]
    with SUPPORT_STATIONS.open(encoding="utf-8-sig", newline="") as handle:
        support = [
            {
                "station_id": f"THAIWATER_{row['station_id']}",
                "code": row["station_code"],
                "name_th": row["station_name_th"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "location_group": "support_sensor",
            }
            for row in csv.DictReader(handle)
            if row["operational_decision"].startswith(("priority_1", "priority_2"))
        ]
    for row in reporting:
        row["location_group"] = "reporting_site"
    return reporting + support


def fetch(stations: list[dict[str, str]]) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "latitude": ",".join(row["latitude"] for row in stations),
            "longitude": ",".join(row["longitude"] for row in stations),
            "hourly": ",".join(VARIABLES),
            "timezone": "Asia/Bangkok",
            "forecast_days": "7",
        }
    )
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={"User-Agent": "Codex-MaePingWeather/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    payloads = payload if isinstance(payload, list) else [payload]
    if len(payloads) != len(stations):
        raise RuntimeError(f"expected {len(stations)} location payloads, received {len(payloads)}")
    return payloads


def _forecast_id(model_run: str, location_id: str, variable: str, valid_at: str) -> str:
    raw = "|".join((SOURCE_ID, model_run, location_id, variable, valid_at))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ingest(database: Path, stations: list[dict[str, str]], payloads: list[dict]) -> int:
    if not database.exists():
        build_database(database)

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    model_run = fetched_at.isoformat()
    bangkok = ZoneInfo("Asia/Bangkok")
    inserted = 0

    connection = sqlite3.connect(database)
    try:
        if connection.execute("SELECT COUNT(*) FROM sources WHERE source_id = ?", (SOURCE_ID,)).fetchone()[0] != 1:
            raise RuntimeError(f"source {SOURCE_ID} is missing; rebuild the database from the current registry")

        for station, payload in zip(stations, payloads):
            hourly = payload["hourly"]
            times = hourly["time"]
            for api_variable, (db_variable, fallback_unit) in VARIABLES.items():
                values = hourly[api_variable]
                unit = payload.get("hourly_units", {}).get(api_variable, fallback_unit)
                for valid_time, value in zip(times, values):
                    if value is None:
                        continue
                    valid_at = datetime.fromisoformat(valid_time).replace(tzinfo=bangkok)
                    valid_at_utc = valid_at.astimezone(timezone.utc)
                    if valid_at_utc < fetched_at:
                        continue
                    lead_hours = (valid_at_utc - fetched_at).total_seconds() / 3600.0
                    connection.execute(
                        """
                        INSERT INTO forecasts (
                            forecast_id, source_id, model_name, model_version, model_run,
                            location_id, variable, issued_at, valid_at, lead_hours,
                            period_minutes, value, unit, ensemble_member, ingested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 60, ?, ?, 'deterministic', ?)
                        """,
                        (
                            _forecast_id(model_run, station["station_id"], db_variable, valid_at.isoformat()),
                            SOURCE_ID,
                            MODEL_NAME,
                            None,
                            model_run,
                            station["station_id"],
                            db_variable,
                            fetched_at.isoformat(),
                            valid_at.isoformat(),
                            lead_hours,
                            float(value),
                            unit,
                            fetched_at.isoformat(),
                        ),
                    )
                    inserted += 1
        connection.commit()
    finally:
        connection.close()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    args = parser.parse_args()

    stations = load_located_stations()
    payloads = fetch(stations)
    inserted = ingest(args.database, stations, payloads)
    reporting = sum(row["location_group"] == "reporting_site" for row in stations)
    support = sum(row["location_group"] == "support_sensor" for row in stations)
    missing = 13 - reporting
    print(
        f"reporting_sites={reporting} support_sensors={support} "
        f"missing_coordinate_sites={missing} forecast_rows={inserted}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
