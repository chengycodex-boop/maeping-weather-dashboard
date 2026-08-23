import unittest

from src.metrics import (
    brier_score,
    contingency,
    mae,
    mean_bias,
    percent_bias,
    rmse,
    verification_summary,
    wape,
)


class ContinuousMetricTests(unittest.TestCase):
    def test_continuous_metrics(self):
        actual = [0.0, 10.0, 20.0]
        forecast = [2.0, 8.0, 25.0]
        self.assertAlmostEqual(mae(actual, forecast), 3.0)
        self.assertAlmostEqual(rmse(actual, forecast), (33.0 / 3.0) ** 0.5)
        self.assertAlmostEqual(mean_bias(actual, forecast), 5.0 / 3.0)
        self.assertAlmostEqual(percent_bias(actual, forecast), 100.0 * 5.0 / 30.0)
        self.assertAlmostEqual(wape(actual, forecast), 30.0)

    def test_missing_pairs_are_excluded(self):
        self.assertEqual(mae([1.0, None, 3.0], [2.0, 99.0, None]), 1.0)

    def test_percentage_metrics_are_undefined_for_all_zero_observations(self):
        self.assertIsNone(percent_bias([0.0, 0.0], [0.0, 2.0]))
        self.assertIsNone(wape([0.0, 0.0], [0.0, 2.0]))


class EventMetricTests(unittest.TestCase):
    def test_contingency_scores(self):
        table = contingency([0.0, 12.0, 15.0, 0.0], [11.0, 13.0, 0.0, 0.0], threshold=10.0)
        self.assertEqual((table.hits, table.misses, table.false_alarms, table.correct_negatives), (1, 1, 1, 1))
        self.assertAlmostEqual(table.pod, 0.5)
        self.assertAlmostEqual(table.far, 0.5)
        self.assertAlmostEqual(table.csi, 1.0 / 3.0)

    def test_brier_score(self):
        self.assertAlmostEqual(brier_score([0.8, 0.3], [True, False]), 0.065)

    def test_summary_includes_event_metrics(self):
        summary = verification_summary([0.0, 20.0], [5.0, 15.0], event_threshold=10.0)
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["hits"], 1)
        self.assertEqual(summary["correct_negatives"], 1)


if __name__ == "__main__":
    unittest.main()
