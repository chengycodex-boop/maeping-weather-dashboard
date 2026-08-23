import unittest

from src.train_bias_corrections import calibration_parameter


class BiasCorrectionTests(unittest.TestCase):
    def test_temperature_additive_offset(self):
        method, value, events = calibration_parameter("temperature", [30, 32], [29, 31])
        self.assertEqual(method, "additive_offset")
        self.assertEqual(value, 1.0)
        self.assertEqual(events, 2)

    def test_precipitation_ratio(self):
        method, value, events = calibration_parameter("precipitation", [2, 4, 0], [1, 2, 0])
        self.assertEqual(method, "multiplicative_ratio")
        self.assertEqual(value, 2.0)
        self.assertEqual(events, 2)

    def test_zero_forecast_is_guarded(self):
        _, value, _ = calibration_parameter("precipitation", [1], [0])
        self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
