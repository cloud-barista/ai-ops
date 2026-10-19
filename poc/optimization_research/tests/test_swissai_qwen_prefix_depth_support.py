"""Boundary checks for the posthoc validation-only depth-support audit."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_swissai_qwen_prefix_depth_support import audit_validation_rows  # noqa: E402
from swissai_qwen_reuse_core import HORIZON_US  # noqa: E402


def row(time_us: int, prefix: tuple[int, ...]):
    return (time_us, b"x" * 32, 16 * len(prefix), prefix)


class DepthSupportAuditTests(unittest.TestCase):
    def test_same_timestamp_does_not_match_but_exact_horizon_does(self) -> None:
        h = HORIZON_US
        records = [
            row(h // 2, (9, 90)),  # warmup
            row(h, (7, 70)), row(h, (7, 70)),  # same time only
            row(h, (8, 80)), row(h, (4,)),
            row(2 * h, (8, 80)),
            row(3 * h, (8, 80)),  # future censored, but observable to 2h
        ]
        result = audit_validation_rows(records, 0, 4 * h, depths=(1, 2))
        self.assertEqual(result["time_support"], {
            "warmup_rows": 1, "future_censored_rows": 1,
            "complete_window_rows": 5,
        })
        one = result["depths"]["1"]
        self.assertEqual((one["positive"], one["negative"], one["too_short_rows"]), (2, 3, 0))
        two = result["depths"]["2"]
        self.assertEqual((two["positive"], two["negative"], two["too_short_rows"]), (2, 2, 1))

    def test_more_than_horizon_is_negative(self) -> None:
        h = HORIZON_US
        records = [row(h, (6,)), row(2 * h + 1, (6,))]
        result = audit_validation_rows(records, 0, 4 * h, depths=(1,))
        self.assertEqual(result["depths"]["1"]["positive"], 0)
        self.assertEqual(result["depths"]["1"]["negative"], 2)

    def test_unsorted_input_is_rejected(self) -> None:
        h = HORIZON_US
        with self.assertRaisesRegex(ValueError, "rows_not_time_sorted"):
            audit_validation_rows([row(2 * h, (1,)), row(h, (1,))], 0, 4 * h)


if __name__ == "__main__":
    unittest.main()
