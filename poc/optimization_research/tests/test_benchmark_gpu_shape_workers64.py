"""Worker comparison must report numeric drift separately from decisions."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_gpu_shape_workers64 import output_differences


class OutputDifferencesTests(unittest.TestCase):
    def test_tiny_number_changes_are_still_exact_output_failures(self):
        old = [{"status": "RESEARCH_ESTIMATE", "selected": {
            "gpu_replicas": 1, "cpu_affinity_cores": 2, "predicted_requests_per_second": 10.0}}]
        new = [{"status": "RESEARCH_ESTIMATE", "selected": {
            "gpu_replicas": 1, "cpu_affinity_cores": 2,
            "predicted_requests_per_second": 10.0 + 1e-12}}]
        result = output_differences(old, new)
        self.assertEqual(result["numeric_fields_changed"], 1)
        self.assertEqual(result["other_fields_changed"], 0)
        self.assertEqual(result["decision_status_changed"], 0)
        self.assertEqual(result["selected_shape_changed"], 0)

    def test_shape_change_is_detected(self):
        old = [{"status": "RESEARCH_ESTIMATE", "selected": {
            "gpu_replicas": 1, "cpu_affinity_cores": 2}}]
        new = [{"status": "RESEARCH_ESTIMATE", "selected": {
            "gpu_replicas": 2, "cpu_affinity_cores": 2}}]
        result = output_differences(old, new)
        self.assertEqual(result["selected_shape_changed"], 1)


if __name__ == "__main__":
    unittest.main()
