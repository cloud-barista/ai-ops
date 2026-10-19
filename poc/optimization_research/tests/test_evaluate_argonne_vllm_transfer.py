"""Contract checks for held-out-model shape transfer inputs."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_argonne_vllm_transfer import add_anchors, features  # noqa: E402


class ArgonneTransferInputsTest(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {"source_row_index": 1, "Model": "Qwen/Qwen2-7B",
             "Input Output Length": 128, "Batch Size": 1, "Hardware": "Nvidia H100 GPU",
             "Num of Hardware": 2, "Latency": 1.0},
            {"source_row_index": 2, "Model": "Qwen/Qwen2-7B",
             "Input Output Length": 128, "Batch Size": 1, "Hardware": "AMD MI300X GPU",
             "Num of Hardware": 1, "Latency": 9.0},
            {"source_row_index": 3, "Model": "Qwen/Qwen2-7B",
             "Input Output Length": 128, "Batch Size": 1, "Hardware": "Nvidia A100 GPU",
             "Num of Hardware": 1, "Latency": 2.0},
        ])
        self.hardware = ("AMD MI300X GPU", "Nvidia A100 GPU", "Nvidia H100 GPU")

    def test_anchor_is_chosen_by_shape_not_lowest_latency(self):
        selected = add_anchors(self.rows)
        self.assertEqual(selected.loc[selected.is_anchor, "Hardware"].tolist(),
                         ["AMD MI300X GPU"])
        self.assertTrue((selected.anchor_latency == 9.0).all())

    def test_target_label_change_does_not_change_features(self):
        selected = add_anchors(self.rows)
        modified = self.rows.copy()
        modified.loc[modified.source_row_index == 1, "Latency"] = 200.0
        altered = add_anchors(modified)
        np.testing.assert_array_equal(features(selected, self.hardware),
                                      features(altered, self.hardware))
        np.testing.assert_array_equal(features(selected, self.hardware, True),
                                      features(altered, self.hardware, True))

    def test_ambiguous_shape_is_rejected(self):
        duplicated = pd.concat([self.rows, self.rows.iloc[[1]].assign(source_row_index=4)])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            add_anchors(duplicated)


if __name__ == "__main__":
    unittest.main()
