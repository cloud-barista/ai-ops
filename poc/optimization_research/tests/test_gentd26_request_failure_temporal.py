"""Synthetic guards for the GenTD26 retrospective temporal sensitivity scorer."""

import csv
import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_request_failure_temporal.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_request_failure_temporal", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def request_row(time, status, *, model="M0", group="G0", negative="2", execution="POISON"):
    return {
        "gmt_create": time, "predict_status": status, "predict_type": "TXT_2_IMG",
        "checkpoint_model_version_id": model, "groupId": group,
        "prompt_length": "10", "negative_prompt_length": negative,
        "num_images_per_prompt": "1", "num_inference_steps": "30", "num_lora": "0",
        "exec_time_seconds": execution,
    }


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(MODULE.SOURCE_REQUIRED) + ["exec_time_seconds"])
        writer.writeheader()
        writer.writerows(rows)


def feature_frame(models):
    n = len(models)
    return pd.DataFrame({
        "predict_type": ["TXT_2_IMG"] * n,
        "checkpoint_model_version_id": models,
        "prompt_length": [10.0] * n,
        "negative_prompt_length": [0.0] * n,
        "negative_prompt_missing": [1] * n,
        "num_images_per_prompt": [1.0] * n,
        "num_inference_steps": [30.0] * n,
        "num_lora": [0.0] * n,
    }, columns=MODULE.FEATURE_COLUMNS)


def supported_cohort():
    n = 5_000
    models = ["M0"] * n
    days = [f"2024-12-{4 + i // 1_000:02d}" for i in range(n)]
    labels = pd.DataFrame({
        "source_row": range(2, n + 2),
        "day": days,
        "target": [int(i % 100 < 20) for i in range(n)],  # 1,000 failures; 200/day.
        "groupId": [f"G{i % 20}" for i in range(n)],
    })
    return feature_frame(models), labels


class TemporalFailureStudyTest(unittest.TestCase):
    def test_test_loader_separates_outcomes_and_ignores_execution_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "requests.csv"
            rows = [
                request_row("2024-11-28 00:00:00", "UNKNOWN"),
                request_row("2024-12-03 23:59:59", "UNKNOWN"),
                request_row("2024-12-04 00:00:00", "FAILED", model="M1", group="G1", negative=""),
                request_row("2024-12-05 00:00:00", "PENDING"),
                request_row("2024-12-06 00:00:00", "SUCCEED", model="M2", group="G2"),
            ]
            write_csv(path, rows)
            features, labels, excluded = MODULE.load_test(path)
            self.assertEqual(tuple(features.columns), MODULE.FEATURE_COLUMNS)
            self.assertTrue(set(features.columns).isdisjoint({
                "predict_status", "exec_time_seconds", "target", "groupId", "day", "gmt_create"
            }))
            self.assertEqual(labels.target.tolist(), [1, 0])
            self.assertEqual(labels.source_row.tolist(), [4, 6])
            self.assertEqual(features.negative_prompt_missing.tolist(), [1, 0])
            self.assertEqual(excluded, {"PENDING": 1, "PROCESSING": 0})
            flipped = rows.copy()
            flipped[2] = dict(flipped[2], predict_status="SUCCEED", exec_time_seconds="DIFFERENT")
            flipped[4] = dict(flipped[4], predict_status="FAILED", exec_time_seconds="DIFFERENT")
            write_csv(path, flipped)
            flipped_features, flipped_labels, _ = MODULE.load_test(path)
            pd.testing.assert_frame_equal(features, flipped_features)
            self.assertEqual(flipped_labels.target.tolist(), [0, 1])

    def test_test_loader_rejects_bad_feature_and_date_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "requests.csv"
            write_csv(path, [request_row("2024-12-04", "FAILED", negative="nan")])
            with self.assertRaisesRegex(ValueError, "invalid negative_prompt_length"):
                MODULE.load_test(path)
            write_csv(path, [request_row("2024-12-05", "FAILED"),
                             request_row("2024-12-04", "SUCCEED")])
            with self.assertRaisesRegex(ValueError, "not monotone"):
                MODULE.load_test(path)
            write_csv(path, [request_row("2024-12-09", "FAILED")])
            with self.assertRaisesRegex(ValueError, "outside frozen test dates"):
                MODULE.load_test(path)

    def test_beta1_model_rate_uses_train_only_and_unseen_fallback(self):
        train = pd.DataFrame({
            "checkpoint_model_version_id": ["M0", "M0", "M1", "M1"],
            "target": [1, 0, 1, 1],
        })
        features = feature_frame(["M0", "M1", "NEW"])
        probabilities = MODULE.beta1_model_probabilities(train, features)
        global_rate = (3 + 1) / (4 + 2)
        np.testing.assert_allclose(probabilities, [
            (1 + global_rate) / 3, (2 + global_rate) / 3, global_rate,
        ])
        self.assertNotIn("target", features)

    def test_train_all_failed_model_ids_are_fixed_from_training_only(self):
        rows = []
        for model in MODULE.TRAIN_ALL_FAILED_MODELS:
            rows.extend([{"checkpoint_model_version_id": model, "target": 1}] * 10)
        rows.extend([{"checkpoint_model_version_id": "M0", "target": 0},
                     {"checkpoint_model_version_id": "M0", "target": 1}])
        train = pd.DataFrame(rows)
        self.assertEqual(MODULE.train_all_failed_models(train), MODULE.TRAIN_ALL_FAILED_MODELS)
        train.loc[0, "target"] = 0
        with self.assertRaisesRegex(ValueError, "differ from frozen audit"):
            MODULE.train_all_failed_models(train)

    def test_support_gate_enforces_all_thresholds_before_prediction(self):
        features, labels = supported_cohort()
        support = MODULE.support_gate(features, labels, MODULE.TRAIN_ALL_FAILED_MODELS)
        self.assertTrue(support["supported"])
        self.assertEqual(support["failed"], 1_000)
        for variant in (
            labels.assign(target=0),
            labels.assign(groupId="ONE_GROUP"),
            labels.assign(day="2024-12-04"),
        ):
            with self.assertRaises(ValueError):
                MODULE.support_gate(features, variant, MODULE.TRAIN_ALL_FAILED_MODELS)
        limited = labels.copy()
        limited["target"] = 0
        # Exactly 50 failures over five days and twenty groups.
        failed_indices = [day * 1_000 + (day % 2) * 10 + j
                          for day in range(5) for j in range(10)]
        limited.loc[failed_indices, "target"] = 1
        few_remainder = features.copy()
        few_remainder.loc[failed_indices[:26], "checkpoint_model_version_id"] = MODULE.TRAIN_ALL_FAILED_MODELS[0]
        with self.assertRaisesRegex(ValueError, "remainder_failed_at_least_25"):
            MODULE.support_gate(few_remainder, limited, MODULE.TRAIN_ALL_FAILED_MODELS)

    def test_predict_three_passes_only_allowlisted_features_and_never_fits(self):
        train = pd.DataFrame({
            "checkpoint_model_version_id": ["M0", "M0"],
            "predict_type": ["TXT_2_IMG", "TXT_2_IMG"],
            "target": [0, 1],
        })
        features = feature_frame(["M0", "NEW"])

        class Original:
            @staticmethod
            def feature_frame(frame):
                self.assertEqual(tuple(frame.columns), MODULE.FEATURE_COLUMNS)
                return frame

            @staticmethod
            def clipped(values):
                return np.clip(values, MODULE.EPS, 1 - MODULE.EPS)

            @staticmethod
            def baseline_predictions(training, frame):
                self.assertEqual(training.target.tolist(), [0, 1])
                self.assertEqual(tuple(frame.columns), MODULE.FEATURE_COLUMNS)
                return {"model_type": np.array([0.3, 0.4])}

        class SavedModel:
            def fit(self, *_args, **_kwargs):
                raise AssertionError("model must not be refit")

            def predict_proba(self, frame):
                self.assertEqual(tuple(frame.columns), MODULE.FEATURE_COLUMNS)
                return np.array([[0.8, 0.2], [0.7, 0.3]])

        saved = SavedModel()
        saved.assertEqual = self.assertEqual
        result = MODULE.predict_three(train, features, saved, Original)
        self.assertEqual(tuple(result), MODULE.METHODS)
        np.testing.assert_allclose(result["logistic"], [0.2, 0.3])
        with self.assertRaisesRegex(ValueError, "only the frozen request feature columns"):
            MODULE.predict_three(train, features.assign(target=1), saved, Original)

    def test_cohort_scoring_preserves_alignment_after_subsetting(self):
        original = MODULE.load_original_module()
        labels = pd.DataFrame({
            "source_row": [2, 3, 4, 5],
            "day": ["2024-12-04", "2024-12-04", "2024-12-05", "2024-12-05"],
            "target": [1, 0, 1, 0],
            "groupId": ["G0", "G1", "G0", "G1"],
        })
        probabilities = {name: np.array([0.8, 0.1, 0.6, 0.2]) for name in MODULE.METHODS}
        subset = MODULE._cohort_scores(original, labels, probabilities, np.array([False, True, True, False]))
        self.assertEqual((subset["requests"], subset["failed"]), (2, 1))
        self.assertAlmostEqual(subset["scores"]["logistic"]["brier"], (0.1**2 + 0.4**2) / 2)
        empty = MODULE._cohort_scores(original, labels, probabilities, np.array([False] * 4))
        self.assertIsNone(empty["scores"]["logistic"])

    def test_frozen_manifest_detects_changed_input_before_test_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.csv"
            model = root / "model.joblib"
            source.write_bytes(b"synthetic input")
            model.write_bytes(b"synthetic model")
            fixed = {"source": source, "selection_model": model}
            expected = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in fixed.items()}
            with (mock.patch.object(MODULE, "FIXED_FILES", fixed),
                  mock.patch.object(MODULE, "EXPECTED_SHA256", expected),
                  mock.patch.object(MODULE, "versions", return_value=MODULE.EXPECTED_VERSIONS)):
                output = root / "output"
                MODULE.freeze(output)
                self.assertTrue((output / "freeze_manifest.json").is_file())
                model.write_bytes(b"changed model")
                with self.assertRaisesRegex(ValueError, "selection_model SHA-256 changed"):
                    MODULE.verify_manifest(output)
                with self.assertRaises(FileExistsError):
                    MODULE.freeze(output)

    def test_version_guard_rejects_different_serialization_environment(self):
        with mock.patch.object(MODULE, "versions", return_value={"sklearn": "0.0"}):
            with self.assertRaisesRegex(ValueError, "library versions changed"):
                MODULE.verify_fixed_inputs()

    def test_score_refuses_existing_result_before_reading_test(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            (output / "score").mkdir()
            with mock.patch.object(MODULE, "load_test", side_effect=AssertionError("read test")):
                with self.assertRaises(FileExistsError):
                    MODULE.score(output)


if __name__ == "__main__":
    unittest.main()
