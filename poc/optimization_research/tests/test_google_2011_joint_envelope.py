"""Information and calibration edge cases for the frozen Google follow-up."""

import unittest

import numpy as np
import pandas as pd

from scripts import evaluate_google_2011_joint_envelope as study


class JointEnvelopeTest(unittest.TestCase):
    def test_job_equal_quantile_does_not_let_large_job_dominate(self):
        frame = pd.DataFrame({"job": ["large"] * 100 + ["small"]})
        scores = np.array([0.0] * 100 + [2.0])
        self.assertEqual(study.equal_job_quantile(scores, frame), 2.0)

    def test_cpu_only_cannot_repair_memory_failure(self):
        frame = pd.DataFrame({"job": ["a", "b"], "max_cpu": [1.0, 1.0],
                              "max_memory": [1.0, 3.0]})
        calibrated = study.calibrate(frame, np.array([1.0, 1.0]), np.array([1.0, 1.0]))
        self.assertIsNone(calibrated["cpu_only"])
        self.assertGreater(calibrated["joint"], 0.0)

    def test_positive_cpu_margin_preserves_memory_and_repairs_joint_coverage(self):
        frame = pd.DataFrame({"job": ["a", "b", "c"],
                              "max_cpu": [1.0, 2.0, 3.0],
                              "max_memory": [1.0, 1.0, 1.0]})
        cpu = np.array([1.0, 1.0, 1.0])
        memory = np.array([1.0, 1.0, 1.0])
        margin = study.calibrate(frame, cpu, memory)["cpu_only"]
        self.assertIsNotNone(margin)
        self.assertTrue(np.all(frame.max_cpu.to_numpy() <= study.apply_margin(cpu, margin)))
        np.testing.assert_array_equal(memory, np.array([1.0, 1.0, 1.0]))


if __name__ == "__main__":
    unittest.main()
