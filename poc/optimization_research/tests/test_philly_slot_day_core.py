"""Decision-time feedback and output-contract checks for the reusable core."""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policies.philly_slot_day import calibrate_day  # noqa: E402


class PhillySlotDayCoreTest(unittest.TestCase):
    def setUp(self):
        self.day_start = 2 * 86400
        self.targets = pd.DataFrame([
            {"machine_hash": "machine", "gpu_index": gpu,
             "decision_utc_s": self.day_start, "p0": .2}
            for gpu in (0, 1)])
        self.prior = pd.DataFrame([
            {"machine_hash": "machine", "gpu_index": gpu,
             "decision_utc_s": 86400 + hour * 3600, "p0": .2,
             "y": int(gpu == 0),
             "label_available_at_utc_s": 86400 + hour * 3600 + 1800}
            for hour in range(4) for gpu in (0, 1)])

    def test_slot_shift_uses_only_previous_day_labels(self):
        scored = calibrate_day(self.targets, self.prior, day_start_utc_s=self.day_start)
        self.assertGreater(scored.slot_probability.iloc[0], scored.slot_probability.iloc[1])
        self.assertAlmostEqual(scored.machine_probability.iloc[0],
                               scored.machine_probability.iloc[1])
        self.assertEqual(list(scored.previous_day_slot_rows), [4, 4])
        illegal = self.targets.assign(y=[0, 1])
        with self.assertRaisesRegex(ValueError, "targets_fields_mismatch"):
            calibrate_day(illegal, self.prior, day_start_utc_s=self.day_start)

    def test_unavailable_previous_day_feedback_cannot_change_probability(self):
        late = self.prior.copy()
        late["label_available_at_utc_s"] = self.day_start + 300
        scored = calibrate_day(self.targets, late, day_start_utc_s=self.day_start)
        self.assertEqual(list(scored.slot_probability), [.2, .2])
        self.assertEqual(list(scored.previous_day_machine_rows), [0, 0])

    def test_previous_day_and_completion_boundary(self):
        future = self.prior.copy()
        future.loc[0, "decision_utc_s"] = self.day_start
        with self.assertRaisesRegex(ValueError, "feedback_outside_immediately_previous_day"):
            calibrate_day(self.targets, future, day_start_utc_s=self.day_start)
        last = self.prior.iloc[[0]].copy()
        last["decision_utc_s"] = self.day_start - 300
        last["label_available_at_utc_s"] = self.day_start + 1500
        scored = calibrate_day(self.targets, last, day_start_utc_s=self.day_start)
        self.assertEqual(list(scored.slot_probability), [.2, .2])

    def test_hourly_first_and_duplicate_rejection(self):
        prior = self.prior.copy()
        extra = prior.iloc[[0]].copy()
        extra["decision_utc_s"] = 86400 + 300
        extra["label_available_at_utc_s"] = 86400 + 2100
        extra["y"] = 0
        scored = calibrate_day(self.targets, pd.concat([prior, extra], ignore_index=True),
                               day_start_utc_s=self.day_start)
        self.assertEqual(list(scored.previous_day_slot_rows), [4, 4])
        with self.assertRaisesRegex(ValueError, "feedback_duplicate_decision"):
            calibrate_day(self.targets, pd.concat([prior, prior], ignore_index=True),
                          day_start_utc_s=self.day_start)


if __name__ == "__main__":
    unittest.main()
