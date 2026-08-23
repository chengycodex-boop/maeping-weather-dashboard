"""Fetch approved ThaiWater rainfall and temperature observations.

ThaiWater public graph timestamps do not include an explicit UTC offset. This
ingester applies Asia/Bangkok (+07:00) and marks every value provisional until
the provider's timestamp convention and station metadata are confirmed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from init_db import ROOT, build_database
except ModuleNotFoundError:  # Imported as src.fetch_thaiwater_observations in tests.
    from src.init_db import ROOT, build_database


SHORTLIST = ROOT / "data" / "support_station_shortlist.csv"
SOURCE_ID = "thaiwater_data_service"
BASE_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
BANGKOK = ZoneInfo("Asia/Bangkok")


def load_operational_stations() -> list[dict[str, str]]:
    with SHORTLIST.open(encoding="utf-8-sig", newline="") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row["operational_decision"].startswith(("priority_1", "priority_2"))
        ]


def supports_temperature(station: dict[str, str]) -> bool:
    return station["operational_decision"].startswith("priority_1")


def fetch_json(endpoint: str, parameters: dict[str, str] | None = None) -> dict:
    query = urllib.parse.urlencode(parameters or {})
    url = f"{BASE_URL}/{endpoint}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, headers={"User-Agent": "MaePingWeather/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if payload.get("result") != "OK":
        raise RuntimeError(f"ThaiWater returned a non-OK response for {endpoint}: {payload}")
    return payload


def parse_local_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BANGKOK)
    return parsed


def quality_flag(variable: str, value: float) -> str:
    if variable == "precipitation" and value < 0:
        return "suspect"
    if variable == "temperature" and not -10 <= value <= 55:
        return "suspect"
    return "provisional"


def observation_id(
    location_id: str, variable: str, observed_at: str, period_minutes: int
) -> str:
    raw = "|".join((SOURCE_ID, location_id, variable, observed_at, str(period_minutes)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rain_rows(station_id: str, start_date: str, end_date: str) -> list[dict]:
    daily = fetch_json(
        "rain_yesterday_graph",
        {"station_id": station_id, "start_date": start_date, "end_date": end_date},
    ).get("data", [])
    hourly = fetch_json("rain_24h_graph", {"station_id": station_id}).get("data", [])
    rows: list[dict] = []
    for item in daily:
        if item.get("rainfall_value") is None:
            continue
        rows.append(
            {
                "variable": "precipitation",
                "observed_at": parse_local_timestamp(item["rainfall_datetime"]).isoformat(),
                "period_minutes": 1440,
                "value": float(item["rainfall_value"]),
                "unit": "mm",
            }
        )
    for item in hourly:
        if item.get("rainfall_value") is None:
            continue
        rows.append(
            {
                "variable": "precipitation",
                "observed_at": parse_local_timestamp(item["rainfall_datetime"]).isoformat(),
                "period_minutes": 60,
                "value": float(item["rainfall_value"]),
                "unit": "mm",
            }
        )
    return rows


def _temperature_rows(
    station_id: str, start_date: str, end_date: str, period_minutes: int
) -> list[dict]:
    payload = fetch_json(
        "temperature_graph",
        {"station_id": station_id, "start_date": start_date, "end_date": end_date},
    )
    graph = payload.get("data", {}).get("graph_data", [])
    return [
        {
            "variable": "temperature",
            "observed_at": parse_local_timestamp(item["datetime"]).isoformat(),
            "period_minutes": period_minutes,
            "value": float(item["value"]),
            "unit": "°C",
        }
        for item in graph
        if item.get("value") is not None
    ]


def fetch_station_rows(station: dict[str, str], now: datetime) -> list[dict]:
    end_date = now.astimezone(BANGKOK).date()
    rain_start = end_date - timedelta(days=30)
    temperature_start = end_date - timedelta(days=7)
    temperature_end = end_date + timedelta(days=1)
    temperature_period = 180 if station["station_code"] == "48377" else 60
    rows = _rain_rows(station["station_id"], rain_start.isoformat(), end_date.isoformat())
    if supports_temperature(station):
        rows += _temperature_rows(
            station["station_id"],
            temperature_start.isoformat(),
            temperature_end.isoformat(),
            temperature_period,
        )
    return rows


def ingest(database: Path, station: dict[str, str], rows: list[dict], ingested_at: str) -> int:
    location_id = f"THAIWATER_{station['station_id']}"
    connection = sqlite3.connect(database)
    try:
        if connection.execute(
            "SELECT COUNT(*) FROM locations WHERE location_id = ?", (location_id,)
        ).fetchone()[0] != 1:
            raise RuntimeError(f"location {location_id} is missing; rebuild the database")
        for row in rows:
            flag = quality_flag(row["variable"], row["value"])
            oid = observation_id(
                location_id, row["variable"], row["observed_at"], row["period_minutes"]
            )
            connection.execute(
                """
                INSERT INTO observations (
                    observation_id, source_id, location_id, variable, observed_at,
                    period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'point', ?)
                ON CONFLICT(source_id, location_id, variable, observed_at, period_minutes)
                DO UPDATE SET
                    value = excluded.value,
                    unit = excluded.unit,
                    quality_flag = excluded.quality_flag,
                    ingested_at = excluded.ingested_at
                """,
                (
                    oid,
                    SOURCE_ID,
                    location_id,
                    row["variable"],
                    row["observed_at"],
                    row["period_minutes"],
                    row["value"],
                    row["unit"],
                    flag,
                    ingested_at,
                ),
            )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    args = parser.parse_args()
    if not args.database.exists():
        build_database(args.database)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    total = 0
    stations = load_operational_stations()
    for station in stations:
        rows = fetch_station_rows(station, now)
        count = ingest(args.database, station, rows, now.isoformat())
        total += count
        rain = sum(row["variable"] == "precipitation" for row in rows)
        temperature = sum(row["variable"] == "temperature" for row in rows)
        print(
            f"station={station['station_code']} rain_rows={rain} "
            f"temperature_rows={temperature} upserted={count}"
        )
    rain_temperature = sum(supports_temperature(station) for station in stations)
    rain_only = len(stations) - rain_temperature
    print(
        f"operational_stations={len(stations)} rain_temperature={rain_temperature} "
        f"rain_only={rain_only} observation_rows_upserted={total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
