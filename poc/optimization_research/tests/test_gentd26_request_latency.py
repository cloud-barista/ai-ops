"""Guard the request trace experiment against time and feature leakage."""

import importlib.util
import pathlib
import unittest

import numpy as np
import pandas as pd


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_request_latency.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_request_latency", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RequestLatencyTraceTest(unittest.TestCase):
    def test_rolling_uses_only_outcomes_past_assumed_availability(self):
        history = pd.DataFrame(
            {"t": pd.to_datetime(["2024-01-01 00:00:00"]),
             "exec_time_seconds": [10.0], "base_p90": [10.0]}
        )
        evaluation = pd.DataFrame(
            {"t": pd.to_datetime([
                "2024-01-01 00:00:01", "2024-01-01 00:00:02",
                "2024-01-01 00:00:05", "2024-01-01 00:00:11",
                "2024-01-01 00:01:42",
            ]),
             "exec_time_seconds": [100.0, 2.0, 1.0, 1.0, 1.0],
             "base_p90": [1.0, 1.0, 1.0, 1.0, 1.0]}
        )
        offsets = MODULE.causal_rolling_offsets(history, evaluation, window=1)
        np.testing.assert_allclose(offsets, [0.0, 0.0, 1.0, 0.0, 99.0])
        delayed = MODULE.causal_rolling_offsets(
            history, evaluation, window=1, feedback_delay_seconds=3600
        )
        np.testing.assert_allclose(delayed, [0.0, 0.0, 0.0, 0.0, 0.0])

    def test_unknown_model_stays_unknown(self):
        train = pd.DataFrame({
            "predict_type": ["TXT_2_IMG"],
            "checkpoint_model_version_id": ["M0"],
            "prompt_length": [10.0],
            "negative_prompt_length": [0.0],
            "negative_prompt_missing": [1.0],
            "num_images_per_prompt": [1.0],
            "num_inference_steps": [30.0],
            "num_lora": [0.0],
            "image_steps": [30.0],
        })
        state = MODULE.fit_feature_state(train)
        future = train.copy()
        future["checkpoint_model_version_id"] = "M1"
        encoded = MODULE.encode_features(future, state)
        self.assertEqual(encoded.shape, (1, 9))
        self.assertEqual(encoded[0, 1], -1)

    def test_window_selection_excludes_labels_unavailable_at_test_start(self):
        boundary = MODULE.VALID_END
        frame = pd.DataFrame({
            "t": [boundary - pd.Timedelta(seconds=3700),
                  boundary - pd.Timedelta(seconds=30)],
            "exec_time_seconds": [10.0, 10.0],
        })
        available = MODULE.assumed_feedback_time(frame, 3600).le(boundary)
        self.assertEqual(available.tolist(), [True, False])
        with self.assertRaises(ValueError):
            MODULE.assumed_feedback_time(frame, -1)

    def test_training_excludes_labels_unavailable_at_fit_boundary(self):
        boundary = MODULE.TRAIN_END
        frame = pd.DataFrame({
            "t": [
                boundary - pd.Timedelta(hours=2),
                boundary - pd.Timedelta(hours=1, seconds=10),
                boundary - pd.Timedelta(hours=1),
                boundary - pd.Timedelta(minutes=1),
                boundary,
            ],
            "exec_time_seconds": [10.0, 10.0, 10.0, 30.0, 1.0],
            "marker": [0, 1, 2, 3, 4],
        })
        train_1h = MODULE.training_rows_available_at_cutoff(frame, boundary, 3600)
        train_0h = MODULE.training_rows_available_at_cutoff(frame, boundary, 0)
        self.assertEqual(train_1h.marker.tolist(), [0, 1])
        self.assertEqual(train_0h.marker.tolist(), [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
