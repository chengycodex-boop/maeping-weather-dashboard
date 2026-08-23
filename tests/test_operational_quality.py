import unittest

from src.check_operational_quality import freshness_level


class OperationalQualityTests(unittest.TestCase):
    def test_freshness_thresholds(self):
        self.assertEqual(freshness_level(None), "critical")
        self.assertEqual(freshness_level(2), "ok")
        self.assertEqual(freshness_level(6), "ok")
        self.assertEqual(freshness_level(6.1), "warning")
        self.assertEqual(freshness_level(24), "warning")
        self.assertEqual(freshness_level(24.1), "critical")


if __name__ == "__main__":
    unittest.main()
