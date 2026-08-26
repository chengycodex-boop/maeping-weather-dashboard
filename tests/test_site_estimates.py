import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.build_site_estimates import build_estimates, build_rainfall_24h
from src.init_db import build_database


class SiteEstimateTests(unittest.TestCase):
    def test_site_estimates_cover_all_reporting_sites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._assert_coverage(Path(directory) / "weather.db")

    def _assert_coverage(self, database: Path) -> None:
        build_database(database)

        import sqlite3

        connection = sqlite3.connect(database)
        try:
            issued = "2026-08-23T08:00:00+00:00"
            valid = "2026-08-23T15:00:00+07:00"
            for location_id, in connection.execute(
            """
            SELECT location_id FROM locations
            WHERE location_id NOT LIKE 'THAIWATER_%'
              AND coordinate_role NOT IN ('grid_centroid', 'unknown')
            """
            ):
                for variable, value, unit in (
                ("precipitation", 1.2, "mm"),
                ("temperature", 29.0, "°C"),
                ):
                    forecast_id = f"{location_id}-{variable}"
                    connection.execute(
                    """
                    INSERT INTO forecasts (
                        forecast_id, source_id, model_name, model_run, location_id,
                        variable, issued_at, valid_at, lead_hours, period_minutes,
                        value, unit, ingested_at
                    ) VALUES (?, 'open_meteo_best_match', 'test', ?, ?, ?, ?, ?, 0, 60, ?, ?, ?)
                    """,
                        (forecast_id, issued, location_id, variable, issued, valid, value, unit, issued),
                    )
            for station_id in ("THAIWATER_11567345", "THAIWATER_1198301"):
                rainfall_end = datetime.fromisoformat(valid)
                for offset in range(24):
                    observed_at = (rainfall_end - timedelta(hours=offset)).isoformat()
                    connection.execute(
                    """
                    INSERT INTO observations (
                        observation_id, source_id, location_id, variable, observed_at,
                        period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                    ) VALUES (?, 'thaiwater_data_service', ?, 'precipitation', ?, 60, 1.0,
                              'mm', 'provisional', 'point', ?)
                    """,
                        (f"{station_id}-rain-{offset}", station_id, observed_at, issued),
                    )
                connection.execute(
                """
                INSERT INTO observations (
                    observation_id, source_id, location_id, variable, observed_at,
                    period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                ) VALUES (?, 'thaiwater_data_service', ?, 'temperature', ?, 60, 28.0,
                          '°C', 'provisional', 'point', ?)
                """,
                    (f"{station_id}-temp", station_id, valid, issued),
                )
            connection.commit()
        finally:
            connection.close()

        rows = build_estimates(database)
        rainfall_24h = build_rainfall_24h(
            database, now=datetime.fromisoformat(valid) + timedelta(hours=1)
        )
        self.assertEqual(len(rows), 26)
        self.assertEqual(len({row["location_id"] for row in rows}), 13)
        approximate = [row for row in rows if row["location_id"] in {"MP08", "MP12"}]
        self.assertTrue(all(row["estimate_type"] != "regional_fallback" for row in approximate))
        self.assertTrue(all(row["spatial_basis"] == "area_anchor" for row in approximate))
        self.assertTrue(all(row["confidence_level"] == "low" for row in approximate))
        self.assertTrue(all(row["historical_error_percent"] is None for row in rows))
        self.assertEqual(len(rainfall_24h), 13)
        self.assertTrue(all(row["period_minutes"] == 1440 for row in rainfall_24h))
        self.assertTrue(all(row["coverage_hours"] == 24 for row in rainfall_24h))
        self.assertTrue(all(row["value"] == 24.0 for row in rainfall_24h))
        approximate_24h = [
            row for row in rainfall_24h if row["location_id"] in {"MP08", "MP12"}
        ]
        self.assertTrue(all(row["confidence_level"] == "low" for row in approximate_24h))

        stale = build_rainfall_24h(
            database, now=datetime.fromisoformat(valid) + timedelta(hours=7)
        )
        self.assertEqual(stale, [])
        connection = sqlite3.connect(database)
        try:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM site_rainfall_24h_latest"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
