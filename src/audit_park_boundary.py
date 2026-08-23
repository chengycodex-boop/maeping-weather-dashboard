# -*- coding: utf-8 -*-
"""Cross-check the provisional OSM park boundary against a government GIS layer."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

try:
    from build_park_grid import generate_grid
    from discover_thaiwater_stations import PARK_URL, fetch_json, geometry_distance_km
    from init_db import ROOT
except ModuleNotFoundError:
    from src.build_park_grid import generate_grid
    from src.discover_thaiwater_stations import PARK_URL, fetch_json, geometry_distance_km
    from src.init_db import ROOT


GOVERNMENT_LAYER = (
    "https://gistdaportal.gistda.or.th/data/rest/services/"
    "L10_Forest/L10_NPRK_MNRE_50k/MapServer/0/query"
    "?where=objectid%3D1&outFields=objectid%2Cname&returnGeometry=true"
    "&outSR=4326&f=geojson"
)
DNP_CATALOG = "https://catalog.dnp.go.th/dataset/141a61a6-e744-45e2-bde5-4449c1068da3"
OUTPUT = ROOT / "data" / "boundary_comparison_latest.json"


def polygon_area_km2(geometry: dict) -> float:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    points = [point for polygon in polygons for ring in polygon for point in ring]
    reference_lat = sum(point[1] for point in points) / len(points)
    scale_x = 111.32 * math.cos(math.radians(reference_lat))
    scale_y = 111.32
    total = 0.0
    for polygon in polygons:
        for index, ring in enumerate(polygon):
            area = abs(sum(
                (ring[i][0] * scale_x) * (ring[(i + 1) % len(ring)][1] * scale_y)
                - (ring[(i + 1) % len(ring)][0] * scale_x) * (ring[i][1] * scale_y)
                for i in range(len(ring))
            ) / 2)
            total += area if index == 0 else -area
    return total


def _distance_to_line(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    fraction = max(0.0, min(1.0, fraction))
    return math.hypot(point[0] - (start[0] + fraction * dx), point[1] - (start[1] + fraction * dy))


def simplify_ring(ring: list, tolerance: float = 0.0005) -> list:
    closed = ring[0] == ring[-1]
    points = ring[:-1] if closed else ring
    if len(points) <= 3:
        return ring

    def simplify(items):
        if len(items) <= 2:
            return items
        distances = [_distance_to_line(point, items[0], items[-1]) for point in items[1:-1]]
        maximum = max(distances, default=0.0)
        if maximum <= tolerance:
            return [items[0], items[-1]]
        split = distances.index(maximum) + 1
        return simplify(items[: split + 1])[:-1] + simplify(items[split:])

    # Start at the point farthest from the first point so a closed ring is not
    # simplified against a zero-length baseline.
    pivot = max(range(1, len(points)), key=lambda i: math.hypot(
        points[i][0] - points[0][0], points[i][1] - points[0][1]
    ))
    first = simplify(points[: pivot + 1])
    second = simplify(points[pivot:] + [points[0]])
    result = first[:-1] + second
    if result[0] != result[-1]:
        result.append(result[0])
    return result


def choose_component(national_geometry: dict, osm_geometry: dict) -> tuple[int, dict]:
    osm_grid = generate_grid(osm_geometry, 5.0)
    sample = [(float(row["longitude"]), float(row["latitude"])) for row in osm_grid]
    polygons = national_geometry["coordinates"] if national_geometry["type"] == "MultiPolygon" else [national_geometry["coordinates"]]
    scored = []
    for index, polygon in enumerate(polygons):
        geometry = {"type": "Polygon", "coordinates": polygon}
        inside = sum(geometry_distance_km(point, geometry)[1] for point in sample)
        if inside:
            scored.append((inside, -abs(polygon_area_km2(geometry) - polygon_area_km2(osm_geometry)), index, geometry))
    if not scored:
        raise RuntimeError("no government polygon overlaps the Mae Ping OSM grid")
    _, _, index, geometry = max(scored)
    return index, geometry


def build_audit() -> dict:
    osm = fetch_json(PARK_URL)["geometry"]
    national = fetch_json(GOVERNMENT_LAYER)["features"][0]["geometry"]
    component_index, government = choose_component(national, osm)
    osm_grid = generate_grid(osm, 5.0)
    government_grid = generate_grid(government, 5.0)
    osm_inside_government = sum(
        geometry_distance_km((float(row["longitude"]), float(row["latitude"])), government)[1]
        for row in osm_grid
    )
    government_inside_osm = sum(
        geometry_distance_km((float(row["longitude"]), float(row["latitude"])), osm)[1]
        for row in government_grid
    )
    simplified = {
        "type": "Polygon",
        "coordinates": [simplify_ring(ring) for ring in government["coordinates"]],
    }
    return {
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "decision": "retain_osm_provisional_pending_current_dnp_geometry",
        "reason": "DNP catalog is authoritative but its public resources expose PDF/XLSX, not the advertised Shapefile; the GISTDA/MNRE layer is dated 2557.",
        "osm": {
            "source": "OpenStreetMap relation 6004000",
            "url": "https://www.openstreetmap.org/relation/6004000",
            "area_km2_approx": round(polygon_area_km2(osm), 2),
            "grid_5km_cells": len(osm_grid),
        },
        "government_reference": {
            "source": "GISTDA/MNRE L10_NPRK_MNRE_50k",
            "source_year_be": 2557,
            "url": GOVERNMENT_LAYER,
            "dnp_catalog_url": DNP_CATALOG,
            "national_component_index": component_index,
            "area_km2_approx": round(polygon_area_km2(government), 2),
            "grid_5km_cells": len(government_grid),
            "geometry": simplified,
        },
        "agreement": {
            "osm_grid_centroids_inside_government": osm_inside_government,
            "osm_grid_centroids_total": len(osm_grid),
            "government_grid_centroids_inside_osm": government_inside_osm,
            "government_grid_centroids_total": len(government_grid),
            "area_difference_percent_vs_government": round(
                (polygon_area_km2(osm) - polygon_area_km2(government))
                / polygon_area_km2(government) * 100,
                2,
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = build_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    agreement = payload["agreement"]
    print(
        f"boundary_audit={args.output} osm_area_km2={payload['osm']['area_km2_approx']} "
        f"government_area_km2={payload['government_reference']['area_km2_approx']} "
        f"osm_grid_inside_government={agreement['osm_grid_centroids_inside_government']}/"
        f"{agreement['osm_grid_centroids_total']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
