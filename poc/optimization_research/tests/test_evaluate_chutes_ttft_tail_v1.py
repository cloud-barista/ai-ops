import unittest

import numpy as np
import pandas as pd

from scripts.evaluate_chutes_ttft_tail_v1 import (
    Bins, DELTA5, _parts30, registered_models, support_gate,
)


MODEL = "model-a"


def make_row(request_id, start, finish, instance, ttft=1.0):
    return {
        "invocation_id": request_id,
        "function_name": "chat_stream",
        "chute_id": MODEL,
        "instance_id": instance,
        "started_at": start,
        "completed_at": finish,
        "it": 100.0,
        "ot": 10.0,
        "ct": 25.0,
        "ttft": ttft,
    }


class ChutesCausalWindowTests(unittest.TestCase):
    def test_unfinished_instance_is_not_available_but_arrival_is(self):
        t = pd.Timestamp("1970-12-28 00:00:00")
        frame = pd.DataFrame([
            make_row(1, t - pd.Timedelta(minutes=2), t - pd.Timedelta(minutes=1), "known"),
            make_row(2, t - pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=1), "future"),
            make_row(3, t - pd.Timedelta(seconds=30), t - pd.Timedelta(seconds=5), None),
        ])
        bins = Bins()
        bins.add_frame(frame)
        bins.finish()
        feedback, instances, tokens, arrivals = _parts30(bins, MODEL, t)
        self.assertEqual(arrivals, 3)
        self.assertEqual(instances, {"known": 1})
        self.assertEqual(sum(len(part) for part in feedback), 2)
        self.assertEqual(tokens[3], 2)
        self.assertEqual(bins.missing_instance_starts[(MODEL, t - DELTA5)], 1)

    def test_label_matures_only_within_sixty_minutes_after_window(self):
        t = pd.Timestamp("1970-12-28 00:00:00")
        frame = pd.DataFrame([
            make_row(1, t, t + pd.Timedelta(minutes=2), "a"),
            make_row(2, t + pd.Timedelta(seconds=1), t + pd.Timedelta(minutes=66), "b", ttft=2.0),
        ])
        bins = Bins()
        bins.add_frame(frame)
        bins.finish()
        self.assertEqual(len(bins.labels[(MODEL, t)]), 1)
        self.assertEqual(float(bins.labels[(MODEL, t)][0]), 1.0)

    def test_duplicate_request_id_across_groups_is_rejected(self):
        t = pd.Timestamp("1970-12-28 00:00:00")
        bins = Bins()
        bins.add_frame(pd.DataFrame([make_row(7, t, t + pd.Timedelta(seconds=3), "a")]))
        bins.add_frame(pd.DataFrame([make_row(7, t + DELTA5, t + DELTA5 + pd.Timedelta(seconds=3), "a")]))
        with self.assertRaisesRegex(ValueError, "duplicate invocation_id"):
            bins.finish()

    def test_out_of_order_row_group_is_rejected(self):
        t = pd.Timestamp("1970-12-28 00:00:00")
        bins = Bins()
        bins.add_frame(pd.DataFrame([make_row(1, t, t + pd.Timedelta(seconds=3), "a")]))
        with self.assertRaisesRegex(ValueError, "globally ordered"):
            bins.add_frame(pd.DataFrame([
                make_row(2, t - DELTA5, t - DELTA5 + pd.Timedelta(seconds=3), "a")
            ]))

    def test_train_registration_needs_past_eligible_windows(self):
        bins = Bins()
        start = pd.Timestamp("1970-12-27 23:15:00")
        for step in range(12):
            bins.labels[(MODEL, start + step * DELTA5)] = np.ones(100, dtype=np.float32)
        self.assertEqual(registered_models(bins), [])

    def test_test_support_keeps_validation_frozen_models(self):
        supports = {
            "train": {"conditional_scored_rows": 500},
            "test": {
                "past_eligible_fraction": 0.8,
                "conditional_scored_rows": 300,
                "cache_coverage_90pct_eligible_fraction": 1.0,
                "scored_windows_per_model": {"a": 20, "b": 11},
            },
        }
        passed, models, reasons = support_gate(
            supports, ["a", "b"], "test", validation_models=["a", "b"]
        )
        self.assertFalse(passed)
        self.assertEqual(models, ["a", "b"])
        self.assertTrue(any("validation-frozen" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
