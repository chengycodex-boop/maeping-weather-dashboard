import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.init_db import build_database
from src.sync_supabase import required_environment, restore_rows, sqlite_rows


class SupabaseSyncTests(unittest.TestCase):
    def test_rejects_publishable_key_for_server_sync(self):
        with patch.dict(
            os.environ,
            {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SECRET_KEY": "sb_publishable_test"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                required_environment()

    def test_accepts_secret_key_without_printing_or_transforming_it(self):
        key = "sb_secret_example_value"
        with patch.dict(
            os.environ,
            {"SUPABASE_URL": "https://example.supabase.co/", "SUPABASE_SECRET_KEY": key},
            clear=True,
        ):
            url, actual = required_environment()
        self.assertEqual(url, "https://example.supabase.co")
        self.assertEqual(actual, key)

    def test_restore_rows_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            row = {
                "observation_id": "test-observation",
                "source_id": "thaiwater_data_service",
                "location_id": "THAIWATER_11111",
                "variable": "temperature",
                "observed_at": datetime(2026, 8, 23, tzinfo=timezone.utc).isoformat(),
                "period_minutes": 60,
                "value": 29.5,
                "unit": "°C",
                "quality_flag": "provisional",
                "spatial_support": "point",
                "ingested_at": datetime(2026, 8, 23, tzinfo=timezone.utc).isoformat(),
            }
            connection = sqlite3.connect(database)
            try:
                restore_rows(connection, "observations", [row])
                restore_rows(connection, "observations", [{**row, "value": 30.0}])
                connection.commit()
                count, value = connection.execute(
                    "SELECT COUNT(*), MAX(value) FROM observations WHERE observation_id=?",
                    (row["observation_id"],),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(count, 1)
            self.assertEqual(value, 30.0)

    def test_forecast_sync_keeps_only_verifiable_operational_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            connection = sqlite3.connect(database)
            try:
                base = {
                    "source_id": "open_meteo_best_match",
                    "model_name": "test",
                    "model_version": None,
                    "model_run": "2026-08-23T00:00:00+00:00",
                    "issued_at": "2026-08-23T00:00:00+00:00",
                    "valid_at": "2026-08-24T00:00:00+00:00",
                    "period_minutes": 60,
                    "value": 1.0,
                    "unit": "mm",
                    "ensemble_member": "deterministic",
                    "quantile": None,
                    "ingested_at": "2026-08-23T00:00:00+00:00",
                }
                rows = [
                    ("keep", "THAIWATER_11567345", "precipitation", 24, "2026-08-24T00:00:00+00:00"),
                    ("reporting", "HQ", "precipitation", 24, "2026-08-24T00:00:00+00:00"),
                    ("long-lead", "THAIWATER_11567345", "precipitation", 96, "2026-08-27T00:00:00+00:00"),
                    ("humidity", "THAIWATER_11567345", "relative_humidity", 24, "2026-08-24T00:00:00+00:00"),
                ]
                connection.executemany(
                    """INSERT INTO forecasts (
                           forecast_id, source_id, model_name, model_version, model_run,
                           location_id, variable, issued_at, valid_at, lead_hours,
                           period_minutes, value, unit, ensemble_member, quantile, ingested_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            forecast_id,
                            base["source_id"], base["model_name"], base["model_version"],
                            base["model_run"], location_id, variable, base["issued_at"],
                            valid_at, lead, base["period_minutes"], base["value"],
                            base["unit"], base["ensemble_member"], base["quantile"],
                            base["ingested_at"],
                        )
                        for forecast_id, location_id, variable, lead, valid_at in rows
                    ],
                )
                connection.commit()
                synced = sqlite_rows(connection, "forecasts")
            finally:
                connection.close()
            self.assertEqual([row["forecast_id"] for row in synced], ["keep"])


if __name__ == "__main__":
    unittest.main()
