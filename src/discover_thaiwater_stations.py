#!/usr/bin/env python3
"""Discover ThaiWater rainfall stations around Mae Ping National Park.

The script uses the public JSON endpoint used by thaiwater.net and the
provisional OpenStreetMap park polygon already recorded by this project.  It
prints CSV to stdout by default so the result can be reviewed before it is
accepted into the station registry.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.request
from datetime import datetime
from typing import Any, Iterable, Sequence


RAIN_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
PARK_URL = (
    "https://nominatim.openstreetmap.org/details.php"
    "?osmtype=R&osmid=6004000&format=json&polygon_geojson=1"
)
USER_AGENT = "maeping-weather-research/1.0"
EARTH_RADIUS_KM = 6371.0088


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def project(lon: float, lat: float, reference_lat: float) -> tuple[float, float]:
    """Project lon/lat to local kilometres using an equirectangular plane."""
    x = math.radians(lon) * EARTH_RADIUS_KM * math.cos(math.radians(reference_lat))
    y = math.radians(lat) * EARTH_RADIUS_KM
    return x, y


def point_in_ring(point: tuple[float, float], ring: Sequence[Sequence[float]]) -> bool:
    lon, lat = point
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > lat) != (y2 > lat)
        if crosses:
            x_at_lat = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < x_at_lat:
                inside = not inside
        previous = current
    return inside


def distance_to_segment_km(
    point: tuple[float, float],
    start: Sequence[float],
    end: Sequence[float],
    reference_lat: float,
) -> float:
    px, py = project(point[0], point[1], reference_lat)
    ax, ay = project(float(start[0]), float(start[1]), reference_lat)
    bx, by = project(float(end[0]), float(end[1]), reference_lat)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    fraction = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    fraction = max(0.0, min(1.0, fraction))
    closest_x = ax + fraction * dx
    closest_y = ay + fraction * dy
    return math.hypot(px - closest_x, py - closest_y)


def distance_to_ring_km(
    point: tuple[float, float], ring: Sequence[Sequence[float]]
) -> float:
    reference_lat = point[1]
    return min(
        distance_to_segment_km(point, ring[index - 1], ring[index], reference_lat)
        for index in range(len(ring))
    )


def polygon_distance_km(
    point: tuple[float, float], polygon: Sequence[Sequence[Sequence[float]]]
) -> tuple[float, bool]:
    outer = polygon[0]
    holes = polygon[1:]
    inside = point_in_ring(point, outer) and not any(
        point_in_ring(point, hole) for hole in holes
    )
    if inside:
        return 0.0, True
    rings: Iterable[Sequence[Sequence[float]]] = [outer, *holes]
    return min(distance_to_ring_km(point, ring) for ring in rings), False


def geometry_distance_km(
    point: tuple[float, float], geometry: dict[str, Any]
) -> tuple[float, bool]:
    geometry_type = geometry["type"]
    coordinates = geometry["coordinates"]
    if geometry_type == "Polygon":
        return polygon_distance_km(point, coordinates)
    if geometry_type == "MultiPolygon":
        results = [polygon_distance_km(point, polygon) for polygon in coordinates]
        if any(is_inside for _, is_inside in results):
            return 0.0, True
        return min(distance for distance, _ in results), False
    raise ValueError(f"unsupported park geometry: {geometry_type}")


def text_at(value: Any, *keys: str) -> str:
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return "" if value is None else str(value).strip()


def parse_source_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def freshness_status(source_time: datetime | None, reference_time: datetime) -> str:
    if source_time is None:
        return "unknown"
    age_hours = (reference_time - source_time).total_seconds() / 3600
    if age_hours < -1:
        return "future_timestamp"
    if age_hours <= 2:
        return "fresh_2h"
    if age_hours <= 6:
        return "delayed_6h"
    return "stale_over_6h"


def station_rows(
    rain_payload: dict[str, Any],
    park_geometry: dict[str, Any],
    radius_km: float,
    reference_time: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in rain_payload.get("data", []):
        station = item.get("station") or {}
        lat = station.get("tele_station_lat")
        lon = station.get("tele_station_long")
        if lat is None or lon is None:
            continue
        lat = float(lat)
        lon = float(lon)
        distance, is_inside = geometry_distance_km((lon, lat), park_geometry)
        if distance > radius_km:
            continue
        source_time_raw = str(item.get("rainfall_datetime") or "")
        source_time = parse_source_time(source_time_raw)
        if is_inside:
            band = "inside_park"
        elif distance <= 25:
            band = "buffer_25km"
        else:
            band = "buffer_75km"
        rows.append(
            {
                "station_id": station.get("id", ""),
                "station_code": station.get("tele_station_oldcode", ""),
                "station_name_th": text_at(station, "tele_station_name", "th"),
                "agency_th": text_at(item, "agency", "agency_name", "th"),
                "agency_short_th": text_at(item, "agency", "agency_shortname", "th"),
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "distance_to_park_km": f"{distance:.2f}",
                "distance_band": band,
                "subdistrict_th": text_at(item, "geocode", "tumbon_name", "th"),
                "district_th": text_at(item, "geocode", "amphoe_name", "th"),
                "province_th": text_at(item, "geocode", "province_name", "th"),
                "basin_th": text_at(item, "basin", "basin_name", "th"),
                "source_datetime_raw": source_time_raw,
                "freshness_status_assuming_ict": freshness_status(
                    source_time, reference_time
                ),
                "rain_1h_mm_snapshot": item.get("rain_1h", ""),
                "rain_24h_mm_snapshot": item.get("rain_24h", ""),
                "source_endpoint": RAIN_URL,
                "park_boundary_role": "provisional_secondary_osm",
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            float(row["distance_to_park_km"]),
            str(row["agency_short_th"]),
            str(row["station_code"]),
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius-km", type=float, default=75.0)
    parser.add_argument(
        "--reference-time",
        default=datetime.now().strftime("%Y-%m-%d %H:%M"),
        help="Local ICT time used only for a provisional freshness label.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reference_time = datetime.strptime(args.reference_time, "%Y-%m-%d %H:%M")
    rain_payload = fetch_json(RAIN_URL)
    park_payload = fetch_json(PARK_URL)
    rows = station_rows(
        rain_payload,
        park_payload["geometry"],
        args.radius_km,
        reference_time,
    )
    if not rows:
        print("no stations found", file=sys.stderr)
        return 1
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
