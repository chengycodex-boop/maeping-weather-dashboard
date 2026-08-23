# -*- coding: utf-8 -*-
"""Ingest fresh TMD radar QPE values at Mae Ping grid centroids."""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from audit_tmd_qpe import OUTPUT as AUDIT_OUTPUT, URL, parse_header, timestamp_from_filename
    from init_db import ROOT, SCHEMA
except ModuleNotFoundError:
    from src.audit_tmd_qpe import OUTPUT as AUDIT_OUTPUT, URL, parse_header, timestamp_from_filename
    from src.init_db import ROOT, SCHEMA


SOURCE_ID = "tmd_radar_satellite"
PRODUCT_NAME = "TMD Radar Composite QPE Prr60"


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


def ensure_schema(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


def extract_values(archive_bytes: bytes, cells: list[dict]) -> tuple[str, dict, dict[str, float | None]]:
    filename, header = parse_header(archive_bytes)
    north = header["yllcorner"] + header["nrows"] * header["cellsize"]
    targets: dict[int, list[tuple[str, int]]] = {}
    result: dict[str, float | None] = {cell["grid_id"]: None for cell in cells}
    for cell in cells:
        column = int((float(cell["longitude"]) - header["xllcorner"]) / header["cellsize"])
        row = int((north - float(cell["latitude"])) / header["cellsize"])
        if 0 <= row < int(header["nrows"]) and 0 <= column < int(header["ncols"]):
            targets.setdefault(row, []).append((cell["grid_id"], column))
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive, archive.open(filename) as handle:
        for _ in range(6):
            handle.readline()
        maximum_row = max(targets, default=-1)
        for row_index, line in enumerate(handle):
            if row_index > maximum_row:
                break
            if row_index not in targets:
                continue
            values = line.decode("ascii").split()
            for grid_id, column in targets[row_index]:
                value = float(values[column])
                result[grid_id] = None if value == header["nodata_value"] else value
    return filename, header, result


def source_row() -> tuple:
    return (
        SOURCE_ID, "ข้อมูลเรดาร์และดาวเทียม", "TMD", "radar_satellite",
        "radar composite QPE precipitation", "0.01 degree grid", "60 minutes",
        "product dependent", "QPE ASCII ZIP", "primary", "spatial rainfall estimate",
        "active_with_freshness_gate", URL,
        "Provisional radar estimate; 6-hour freshness gate and mountain beam-blockage risk.",
    )


def ingest(database: Path, product_time: datetime, values: dict[str, float | None]) -> int:
    connection = sqlite3.connect(database)
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        connection.execute(
            """INSERT INTO sources (
                   source_id, source_name, provider, source_class, variables,
                   spatial_grain, temporal_grain, latency, access_mode,
                   authority_tier, operational_role, status, url, limitations
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET status=excluded.status,
                   url=excluded.url, limitations=excluded.limitations""",
            source_row(),
        )
        updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        connection.execute("DELETE FROM grid_estimates_latest WHERE source_id=?", (SOURCE_ID,))
        rows = [
            (grid_id, SOURCE_ID, PRODUCT_NAME, product_time.isoformat(), "precipitation",
             60, value, "mm", "provisional", updated_at)
            for grid_id, value in values.items() if value is not None and value >= 0
        ]
        connection.executemany(
            """INSERT INTO grid_estimates_latest (
                   grid_id, source_id, product_name, observed_at, variable,
                   period_minutes, value, unit, quality_flag, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def fetch_archive() -> bytes:
    request = urllib.request.Request(URL, headers={"User-Agent": "Codex-MaePingWeather/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def write_audit(payload: dict) -> None:
    AUDIT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("--max-age-hours", type=float, default=6.0)
    args = parser.parse_args()
    ensure_schema(args.database)
    archive_bytes = fetch_archive()
    cells = load_cells(args.database)
    filename, header, values = extract_values(archive_bytes, cells)
    product_time = timestamp_from_filename(filename)
    checked_at = datetime.now(timezone.utc).replace(microsecond=0)
    age_hours = (checked_at - product_time).total_seconds() / 3600
    fresh = -1 <= age_hours <= args.max_age_hours
    ingested = ingest(args.database, product_time, values) if fresh else 0
    payload = {
        "checked_at": checked_at.isoformat(), "source": "TMD Radar composite QPE ASCII",
        "url": URL, "filename": filename, "product_time": product_time.isoformat(),
        "time_assumption": "UTC inferred from TMD radar page convention",
        "age_hours": round(age_hours, 2), "max_age_hours": args.max_age_hours,
        "status": "fresh" if fresh else "stale",
        "ingestion_decision": "ingested" if fresh else "skip_stale_product",
        "ingested_grid_cells": ingested, "available_grid_cells": sum(v is not None for v in values.values()),
        "product": "60-minute radar composite quantitative precipitation estimate", "unit": "mm",
        "grid": {"ncols": int(header["ncols"]), "nrows": int(header["nrows"]),
                 "cellsize_degrees": header["cellsize"], "west": header["xllcorner"],
                 "south": header["yllcorner"], "nodata_value": header["nodata_value"]},
    }
    write_audit(payload)
    print(f"tmd_qpe={payload['status']} product_time={payload['product_time']} age_hours={payload['age_hours']} ingested_grid_cells={ingested}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
