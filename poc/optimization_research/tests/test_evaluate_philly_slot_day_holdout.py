"""Frozen cohort selection and new-score boundary checks."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_philly_slot_day_holdout as holdout  # noqa: E402


class PhillySlotHoldoutTest(unittest.TestCase):
    def test_hash_bucket_is_disjoint_from_previous_cohorts(self):
        candidate = next(f"synthetic_machine_{i}" for i in range(1000)
                         if holdout.selected(f"synthetic_machine_{i}"))
        bucket = holdout.collector.machine_bucket(candidate)
        self.assertGreaterEqual(bucket, 35)
        self.assertLess(bucket, 45)
        self.assertFalse(bucket < 35)

    def test_collect_needs_frozen_manifest_before_http(self):
        with patch.object(holdout, "verify_freeze", side_effect=ValueError("missing freeze")):
            with patch.object(holdout.requests, "Session") as session:
                with self.assertRaisesRegex(ValueError, "missing freeze"):
                    holdout.collect()
                session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
