import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.fetch_gistda_disasters import convert_features as convert_gistda
from src.fetch_usgs_earthquakes import convert as convert_usgs
from src.init_db import ROOT, build_database
from src.source_portfolio import ensure_source_portfolio, record_source_health


class SourcePortfolioTests(unittest.TestCase):
    def test_mojiweather_is_not_registered_or_routed(self):
        with (ROOT / "data" / "source_registry.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            source_ids = {row["source_id"] for row in csv.DictReader(handle)}
        with (ROOT / "data" / "source_routes.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            route_source_ids = {row["source_id"] for row in csv.DictReader(handle)}
        self.assertNotIn("moji_weather", source_ids)
        self.assertNotIn("moji_weather", route_source_ids)

    def test_reviewed_portfolio_has_fallbacks_without_double_counting_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            connection = sqlite3.connect(database)
            try:
                routes = connection.execute(
                    "SELECT route_id, fallback_group, independence_group FROM source_routes"
                ).fetchall()
            finally:
                connection.close()
        self.assertEqual(len(routes), 20)
        route_map = {route_id: (fallback, lineage) for route_id, fallback, lineage in routes}
        self.assertEqual(route_map["gistda_fire"][1], route_map["nasa_firms_fire"][1])
        self.assertNotEqual(route_map["ecmwf_forecast"][1], route_map["noaa_forecast"][1])

    def test_portfolio_refresh_does_not_delete_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO observations (
                        observation_id, source_id, location_id, variable, observed_at,
                        period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                    ) VALUES (
                        'keep-me', 'thaiwater_data_service', 'THAIWATER_11567345',
                        'temperature', '2026-08-24T00:00:00+00:00', 60, 28.0,
                        '°C', 'provisional', 'point', '2026-08-24T00:01:00+00:00'
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            counts = ensure_source_portfolio(database)
            connection = sqlite3.connect(database)
            try:
                observations = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            finally:
                connection.close()
        self.assertEqual(observations, 1)
        self.assertEqual(counts["routes"], 20)

    def test_health_is_upserted_by_route(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            for status in ("failed", "success"):
                record_source_health(
                    database,
                    route_id="usgs_earthquakes",
                    cycle_id="cycle-1",
                    status=status,
                    duration_seconds=1.25,
                    records_received=3,
                    newest_source_time="2026-08-24T00:00:00+00:00",
                    freshness_lag_minutes=5,
                    error_code=None,
                    message=status,
                )
            connection = sqlite3.connect(database)
            try:
                rows = connection.execute(
                    "SELECT status, records_received FROM source_health_latest"
                ).fetchall()
            finally:
                connection.close()
        self.assertEqual(rows, [("success", 3)])

    def test_gistda_geojson_is_normalized(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "hotspot-1",
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [98.75, 17.58]},
                    "properties": {
                        "acq_datetime": "2026-08-24T05:00:00Z",
                        "frp": 12.5,
                        "confidence": "nominal",
                    },
                }
            ],
        }
        rows, newest = convert_gistda(payload, "fire", "https://example.test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hazard_type"], "wildfire")
        self.assertEqual(rows[0]["latitude"], 17.58)
        self.assertEqual(rows[0]["value"], 12.5)
        self.assertEqual(newest, "2026-08-24T05:00:00+00:00")
        self.assertEqual(json.loads(rows[0]["properties_json"])["confidence"], "nominal")

    def test_usgs_geojson_is_normalized(self):
        payload = {
            "features": [
                {
                    "id": "us-test",
                    "geometry": {"type": "Point", "coordinates": [99.0, 18.0, 10.0]},
                    "properties": {
                        "time": 1787547600000,
                        "mag": 4.8,
                        "place": "Northern Thailand region",
                        "url": "https://earthquake.usgs.gov/example",
                    },
                }
            ]
        }
        rows, newest = convert_usgs(payload, "https://example.test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hazard_type"], "earthquake")
        self.assertEqual(rows[0]["value"], 4.8)
        self.assertEqual(rows[0]["severity"], "moderate")
        self.assertIsNotNone(newest)


if __name__ == "__main__":
    unittest.main()
