import unittest

from src.evaluate_forecasts import lead_bucket, readiness_status
from src.fetch_open_meteo_baseline import load_located_stations
from src.station_capabilities import verification_variables


class ForecastEvaluationTests(unittest.TestCase):
    def test_lead_bucket_boundaries(self):
        self.assertEqual(lead_bucket(0), "0–6")
        self.assertEqual(lead_bucket(6), "0–6")
        self.assertEqual(lead_bucket(6.1), "7–12")
        self.assertEqual(lead_bucket(24), "13–24")
        self.assertEqual(lead_bucket(168), "121–168")
        self.assertIsNone(lead_bucket(169))

    def test_readiness_requires_days_not_only_pairs(self):
        self.assertEqual(readiness_status(0, 0), "no_pairs")
        self.assertEqual(readiness_status(500, 1), "accumulating")
        self.assertEqual(readiness_status(500, 45), "provisional")
        self.assertEqual(readiness_status(500, 60), "ready")

    def test_forecast_targets_reporting_and_operational_sensor_locations(self):
        locations = load_located_stations()
        self.assertEqual(sum(row["location_group"] == "reporting_site" for row in locations), 11)
        self.assertEqual(sum(row["location_group"] == "support_sensor" for row in locations), 7)

    def test_verification_variables_follow_observation_grain(self):
        self.assertEqual(
            verification_variables("DNP089", "priority_1_rain_temperature"),
            ("precipitation", "temperature"),
        )
        self.assertEqual(
            verification_variables("STN0370", "priority_2_rain_only"),
            ("precipitation",),
        )
        self.assertEqual(
            verification_variables("48377", "priority_1_reference_station"),
            ("temperature",),
        )


if __name__ == "__main__":
    unittest.main()
