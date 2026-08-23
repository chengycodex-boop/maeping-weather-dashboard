# -*- coding: utf-8 -*-
"""Build and seed a deterministic 5-km forecast grid inside Mae Ping park."""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from discover_thaiwater_stations import PARK_URL, fetch_json, geometry_distance_km
    from init_db import ROOT, SCHEMA
except ModuleNotFoundError:
    from src.discover_thaiwater_stations import PARK_URL, fetch_json, geometry_distance_km
    from src.init_db import ROOT, SCHEMA


GRID_CSV = ROOT / "data" / "park_grid_5km.csv"
BOUNDARY_SOURCE = "OpenStreetMap relation 6004000"
BOUNDARY_STATUS = "provisional_secondary_osm"


def geometry_coordinates(geometry: dict) -> list[tuple[float, float]]:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [(float(lon), float(lat)) for polygon in polygons for ring in polygon for lon, lat in ring]


def generate_grid(geometry: dict, spacing_km: float = 5.0) -> list[dict]:
    coordinates = geometry_coordinates(geometry)
    min_lon = min(point[0] for point in coordinates)
    max_lon = max(point[0] for point in coordinates)
    min_lat = min(point[1] for point in coordinates)
    max_lat = max(point[1] for point in coordinates)
    reference_lat = (min_lat + max_lat) / 2
    lat_step = spacing_km / 111.32
    lon_step = spacing_km / (111.32 * math.cos(math.radians(reference_lat)))
    points: list[tuple[float, float]] = []
    latitude = min_lat + lat_step / 2
    while latitude <= max_lat:
        longitude = min_lon + lon_step / 2
        while longitude <= max_lon:
            if geometry_distance_km((longitude, latitude), geometry)[1]:
                points.append((longitude, latitude))
            longitude += lon_step
        latitude += lat_step
    points.sort(key=lambda point: (round(point[1], 8), round(point[0], 8)))
    return [
        {
            "grid_id": f"GRID5K_{index:04d}",
            "code": f"G5-{index:04d}",
            "name_th": f"กริดพยากรณ์ 5 กม. {index:04d}",
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "spacing_km": f"{spacing_km:.1f}",
            "boundary_source": BOUNDARY_SOURCE,
            "boundary_status": BOUNDARY_STATUS,
        }
        for index, (longitude, latitude) in enumerate(points, start=1)
    ]


def write_grid(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_grid(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def seed(database: Path, rows: list[dict[str, str]]) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for row in rows:
            connection.execute(
                """
                INSERT INTO locations (
                    location_id, code, name_th, latitude, longitude,
                    coordinate_role, confidence, verification_status
                ) VALUES (?, ?, ?, ?, ?, 'grid_centroid', 'medium', ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    code=excluded.code, name_th=excluded.name_th,
                    latitude=excluded.latitude, longitude=excluded.longitude,
                    coordinate_role=excluded.coordinate_role,
                    confidence=excluded.confidence,
                    verification_status=excluded.verification_status
                """,
                (row["grid_id"], row["code"], row["name_th"], float(row["latitude"]),
                 float(row["longitude"]), row["boundary_status"]),
            )
            connection.execute(
                """
                INSERT INTO grid_cells (
                    grid_id, spacing_km, boundary_source, boundary_status, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(grid_id) DO UPDATE SET
                    spacing_km=excluded.spacing_km,
                    boundary_source=excluded.boundary_source,
                    boundary_status=excluded.boundary_status
                """,
                (row["grid_id"], float(row["spacing_km"]), row["boundary_source"],
                 row["boundary_status"], created_at),
            )
        connection.commit()
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data" / "maeping_weather.db")
    parser.add_argument("--spacing-km", type=float, default=5.0)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.refresh or not GRID_CSV.exists():
        rows = generate_grid(fetch_json(PARK_URL)["geometry"], args.spacing_km)
        if not rows:
            raise RuntimeError("park grid is empty")
        write_grid(GRID_CSV, rows)
    rows = load_grid(GRID_CSV)
    seed(args.database, rows)
    print(f"grid_cells={len(rows)} spacing_km={args.spacing_km:g} source={BOUNDARY_STATUS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
