import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from swissai_output_tokens_core import (
    DAY_US, build_day_snapshots, higher_quantile, pinball_loss,
    recent_prediction, static_quantiles,
)


class SwissaiOutputTokenCoreTests(unittest.TestCase):
    def test_day_start_excludes_same_day_and_exact_boundary(self):
        # At day 10, only the event available in [day 3, day 10) enters
        # the seven-day history. An event at the decision boundary waits.
        days = np.array([10, 11], dtype=np.int64)
        availability = np.array([3 * DAY_US - 1, 3 * DAY_US,
                                 10 * DAY_US, 10 * DAY_US + 1],
                                dtype=np.int64)
        tokens = np.array([1000, 10, 999, 20], dtype=np.int64)
        codes = np.zeros(4, dtype=np.int32)
        snapshots = build_day_snapshots(days, availability, codes, tokens, 1)
        self.assertEqual(snapshots[10].seven_day.count, 1)
        self.assertEqual(snapshots[10].seven_day.p90, 10)
        self.assertEqual(snapshots[10].one_day.count, 0)
        self.assertEqual(snapshots[11].one_day.count, 2)
        self.assertEqual(snapshots[11].one_day.p90, 999)

    def test_future_labels_cannot_change_earlier_snapshot(self):
        days = np.array([10], dtype=np.int64)
        old = build_day_snapshots(days, np.array([9 * DAY_US]),
                                  np.array([0]), np.array([11]), 1)
        new = build_day_snapshots(days,
                                  np.array([9 * DAY_US, 12 * DAY_US]),
                                  np.array([0, 0]), np.array([11, 9999]), 1)
        self.assertEqual(old[10].seven_day.p90, new[10].seven_day.p90)

    def test_static_and_recent_backoff(self):
        global_p90, by_model = static_quantiles(
            np.array([0, 0], dtype=np.int32),
            np.array([10, 20], dtype=np.int64), 2)
        self.assertEqual(global_p90, 20)
        self.assertEqual(by_model[1], 20)
        snapshots = build_day_snapshots(
            np.array([10]), np.array([9 * DAY_US, 9 * DAY_US]),
            np.array([0, 1]), np.array([10, 20]), 2)
        predictions = recent_prediction(snapshots[10],
                                        np.array([0, 1]), global_p90)
        self.assertGreater(predictions[0], 10)
        self.assertLess(predictions[0], 20)
        self.assertEqual(predictions[1], 20)

    def test_pinball_and_higher_quantile(self):
        self.assertEqual(higher_quantile(np.array([1, 2, 3]), 0.9), 3)
        np.testing.assert_allclose(pinball_loss(np.array([10, 0]),
                                             np.array([0, 10])), [9, 1])

    def test_recent_model_uses_unsmoothed_p90_after_100_labels(self):
        snapshots = build_day_snapshots(
            np.array([10]), np.full(101, 9 * DAY_US),
            np.array([0] * 100 + [1], dtype=np.int32),
            np.array([10] * 100 + [100], dtype=np.int64), 2)
        prediction = recent_prediction(snapshots[10], np.array([0]), 50)
        self.assertEqual(prediction[0], 10)


if __name__ == '__main__':
    unittest.main()
