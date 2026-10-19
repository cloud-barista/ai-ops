"""The three event classes must respect future observability."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_maas_sm_observability_support import classify_future


class ObservabilityClassTests(unittest.TestCase):
    def test_known_high_takes_precedence_over_another_missing_window(self):
        valid = np.array([[True, False, True, True, True, True]])
        high = np.array([[True, True, False, False, False, False]])
        self.assertEqual(classify_future(valid, high).tolist(), [0])

    def test_invalid_high_is_not_treated_as_known(self):
        valid = np.array([[True, False, True, True, True, True]])
        high = np.array([[False, True, False, False, False, False]])
        self.assertEqual(classify_future(valid, high).tolist(), [2])

    def test_six_valid_normal_windows_are_fully_observed_normal(self):
        valid = np.ones((1, 6), dtype=bool)
        high = np.zeros((1, 6), dtype=bool)
        self.assertEqual(classify_future(valid, high).tolist(), [1])

    def test_wrong_future_width_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_future(np.ones((1, 5), dtype=bool), np.zeros((1, 5), dtype=bool))


if __name__ == "__main__":
    unittest.main()
