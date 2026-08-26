import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.fetch_thaiwater_observations import (
    SOURCE_ID,
    canonical_utc_timestamp,
    ingest,
    load_operational_stations,
    observation_id,
    parse_local_timestamp,
    quality_flag,
    supports_temperature,
)
from src.init_db import build_database


class ThaiWaterIngestionTests(unittest.TestCase):
    def test_naive_timestamp_is_attached_to_bangkok(self):
        parsed = parse_local_timestamp("2026-08-23 14:00")
        self.assertEqual(parsed.isoformat(), "2026-08-23T14:00:00+07:00")

    def test_timestamp_is_canonicalized_to_utc(self):
        self.assertEqual(
            canonical_utc_timestamp("2026-08-23T14:00:00+07:00"),
            "2026-08-23T07:00:00+00:00",
        )

    def test_qc_flags_implausible_values_without_dropping_them(self):
        self.assertEqual(quality_flag("precipitation", -0.1), "suspect")
        self.assertEqual(quality_flag("temperature", 60), "suspect")
        self.assertEqual(quality_flag("temperature", 24.5), "provisional")

    def test_observation_id_is_stable_and_period_specific(self):
        hourly = observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 60)
        daily = observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 1440)
        self.assertEqual(hourly, observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 60))
        self.assertNotEqual(hourly, daily)

    def test_observation_id_matches_across_equivalent_timezones(self):
        local = observation_id(
            "THAIWATER_1", "precipitation", "2026-08-23T14:00:00+07:00", 60
        )
        utc = observation_id(
            "THAIWATER_1", "precipitation", "2026-08-23T07:00:00+00:00", 60
        )
        self.assertEqual(local, utc)

    def test_ingest_reconciles_legacy_id_after_supabase_normalizes_time(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            location_id = "THAIWATER_11567345"
            local_time = "2026-08-23T14:00:00+07:00"
            utc_time = "2026-08-23T07:00:00+00:00"
            legacy_raw = "|".join(
                (SOURCE_ID, location_id, "precipitation", local_time, "60")
            )
            legacy_id = hashlib.sha256(legacy_raw.encode("utf-8")).hexdigest()
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO observations (
                        observation_id, source_id, location_id, variable, observed_at,
                        period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                    ) VALUES (?, ?, ?, 'precipitation', ?, 60, 0.0, 'mm',
                              'provisional', 'point', ?)
                    """,
                    (legacy_id, SOURCE_ID, location_id, utc_time, utc_time),
                )
                connection.commit()
            finally:
                connection.close()
            station = {"station_id": "11567345"}
            count = ingest(
                database,
                station,
                [
                    {
                        "variable": "precipitation",
                        "observed_at": local_time,
                        "period_minutes": 60,
                        "value": 2.5,
                        "unit": "mm",
                    }
                ],
                "2026-08-23T08:00:00+00:00",
            )
            connection = sqlite3.connect(database)
            try:
                rows = connection.execute(
                    """SELECT observation_id, observed_at, value FROM observations
                       WHERE location_id=? AND variable='precipitation'""",
                    (location_id,),
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(count, 1)
            self.assertEqual(rows, [(legacy_id, utc_time, 2.5)])

    def test_ingest_normalizes_legacy_local_time_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            build_database(database)
            location_id = "THAIWATER_11567345"
            local_time = "2026-08-23T15:00:00+07:00"
            utc_time = "2026-08-23T08:00:00+00:00"
            legacy_raw = "|".join(
                (SOURCE_ID, location_id, "precipitation", local_time, "60")
            )
            legacy_id = hashlib.sha256(legacy_raw.encode("utf-8")).hexdigest()
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO observations (
                        observation_id, source_id, location_id, variable, observed_at,
                        period_minutes, value, unit, quality_flag, spatial_support, ingested_at
                    ) VALUES (?, ?, ?, 'precipitation', ?, 60, 0.0, 'mm',
                              'provisional', 'point', ?)
                    """,
                    (legacy_id, SOURCE_ID, location_id, local_time, utc_time),
                )
                connection.commit()
            finally:
                connection.close()
            ingest(
                database,
                {"station_id": "11567345"},
                [
                    {
                        "variable": "precipitation",
                        "observed_at": local_time,
                        "period_minutes": 60,
                        "value": 1.5,
                        "unit": "mm",
                    }
                ],
                "2026-08-23T09:00:00+00:00",
            )
            connection = sqlite3.connect(database)
            try:
                rows = connection.execute(
                    """SELECT observed_at, value FROM observations
                       WHERE location_id=? AND variable='precipitation'""",
                    (location_id,),
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(rows, [(utc_time, 1.5)])

    def test_operational_variable_scope_respects_station_capability(self):
        stations = load_operational_stations()
        self.assertEqual(len(stations), 7)
        self.assertEqual(sum(supports_temperature(row) for row in stations), 4)
        self.assertEqual(sum(not supports_temperature(row) for row in stations), 3)


if __name__ == "__main__":
    unittest.main()
