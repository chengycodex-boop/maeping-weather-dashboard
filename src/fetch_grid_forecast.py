# -*- coding: utf-8 -*-
"""Fetch a seven-day Open-Meteo snapshot for every park grid centroid."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from fetch_open_meteo_baseline import ENDPOINT, MODEL_NAME, SOURCE_ID
    from init_db import ROOT
except ModuleNotFoundError:
    from src.fetch_open_meteo_baseline import ENDPOINT, MODEL_NAME, SOURCE_ID
    from src.init_db import ROOT


VARIABLES = {
    "precipitation": ("precipitation", "mm"),
    "precipitation_probability": ("precipitation_probability", "%"),
    "temperature_2m": ("temperature", "°C"),
}


def chunks(rows: list[dict], size: int = 30):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load_cells(database: Path) -> list[dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            """SELECT g.grid_id, l.latitude, l.longitude
               FROM grid_cells g JOIN locations l ON l.location_id=g.grid_id
               ORDER BY g.grid_id"""
        )]
    finally:
        connection.close()


def fetch_batch(cells: list[dict]) -> list[dict]:
    query = urllib.parse.urlencode({
        "latitude": ",".join(str(row["latitude"]) for row in cells),
        "longitude": ",".join(str(row["longitude"]) for row in cells),
        "hourly": ",".join(VARIABLES),
        "timezone": "Asia/Bangkok",
        "forecast_days": "7",
    })
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}", headers={"User-Agent": "Codex-MaePingWeather/1.0"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    payloads = payload if isinstance(payload, list) else [payload]
    if len(payloads) != len(cells):
        raise RuntimeError(f"expected {len(cells)} grid payloads, received {len(payloads)}")
    return payloads


def normalize(cells: list[dict], payloads: list[dict], fetched_at: datetime) -> list[tuple]:
    bangkok = ZoneInfo("Asia/Bangkok")
    model_run = fetched_at.isoformat()
    rows: list[tuple] = []
    for cell, payload in zip(cells, payloads):
        hourly = payload["hourly"]
        for api_variable, (variable, fallback_unit) in VARIABLES.items():
            unit = payload.get("hourly_units", {}).get(api_variable, fallback_unit)
            for valid_time, value in zip(hourly["time"], hourly[api_variable]):
                if value is None:
                    continue
                valid_at = datetime.fromisoformat(valid_time).replace(tzinfo=bangkok)
                if valid_at.astimezone(timezone.utc) < fetched_at:
                    continue
                rows.append((cell["grid_id"], SOURCE_ID, MODEL_NAME, model_run,
                             fetched_at.isoformat(), valid_at.isoformat(), variable,
                             60, float(value), unit, fetched_at.isoformat()))
    return rows


def ingest_snapshot(database: Path, rows: list[tuple]) -> int:
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM grid_forecasts_latest")
        connection.executemany(
            """INSERT INTO grid_forecasts_latest (
                   grid_id, source_id, model_name, model_run, issued_at, valid_at,
                   variable, period_minutes, value, unit, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("--batch-size", type=int, default=30)
    args = parser.parse_args()
    cells = load_cells(args.database)
    if not cells:
        raise RuntimeError("no grid cells; run build_park_grid.py first")
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    rows: list[tuple] = []
    for batch in chunks(cells, args.batch_size):
        rows.extend(normalize(batch, fetch_batch(batch), fetched_at))
    inserted = ingest_snapshot(args.database, rows)
    print(f"grid_cells={len(cells)} latest_grid_forecast_rows={inserted} model_run={fetched_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
