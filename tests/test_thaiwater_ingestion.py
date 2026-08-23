import unittest

from src.fetch_thaiwater_observations import (
    load_operational_stations,
    observation_id,
    parse_local_timestamp,
    quality_flag,
    supports_temperature,
)


class ThaiWaterIngestionTests(unittest.TestCase):
    def test_naive_timestamp_is_attached_to_bangkok(self):
        parsed = parse_local_timestamp("2026-08-23 14:00")
        self.assertEqual(parsed.isoformat(), "2026-08-23T14:00:00+07:00")

    def test_qc_flags_implausible_values_without_dropping_them(self):
        self.assertEqual(quality_flag("precipitation", -0.1), "suspect")
        self.assertEqual(quality_flag("temperature", 60), "suspect")
        self.assertEqual(quality_flag("temperature", 24.5), "provisional")

    def test_observation_id_is_stable_and_period_specific(self):
        hourly = observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 60)
        daily = observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 1440)
        self.assertEqual(hourly, observation_id("THAIWATER_1", "precipitation", "2026-08-23T00:00:00+07:00", 60))
        self.assertNotEqual(hourly, daily)

    def test_operational_variable_scope_respects_station_capability(self):
        stations = load_operational_stations()
        self.assertEqual(len(stations), 7)
        self.assertEqual(sum(supports_temperature(row) for row in stations), 4)
        self.assertEqual(sum(not supports_temperature(row) for row in stations), 3)


if __name__ == "__main__":
    unittest.main()
