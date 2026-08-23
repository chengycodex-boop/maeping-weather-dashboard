import unittest

from src.build_system_readiness import threshold_level, weighted_score


class SystemReadinessTests(unittest.TestCase):
    def test_weighted_score(self):
        value = weighted_score([{"score": 100, "weight": 3}, {"score": 0, "weight": 1}])
        self.assertEqual(value, 75.0)

    def test_threshold_level(self):
        thresholds = {"watch": 20, "warning": 35, "critical": 50}
        self.assertEqual(threshold_level(10, thresholds), "normal")
        self.assertEqual(threshold_level(20, thresholds), "watch")
        self.assertEqual(threshold_level(40, thresholds), "warning")
        self.assertEqual(threshold_level(60, thresholds), "critical")


if __name__ == "__main__":
    unittest.main()
