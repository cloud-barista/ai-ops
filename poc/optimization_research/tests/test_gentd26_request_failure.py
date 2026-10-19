"""Synthetic guards for the validation-only GenTD26 FAILED-status study."""

import csv
import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_request_failure.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_request_failure", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def request_row(t, status, *, model="M0", group="G0", negative="2"):
    return {
        "gmt_create": t, "predict_status": status, "predict_type": "TXT_2_IMG",
        "checkpoint_model_version_id": model, "groupId": group,
        "prompt_length": "10", "negative_prompt_length": negative,
        "num_images_per_prompt": "1", "num_inference_steps": "30", "num_lora": "0",
    }


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(MODULE.REQUIRED))
        writer.writeheader()
        writer.writerows(rows)


def frame():
    return pd.DataFrame({
        "source_row": [2, 3, 4, 5],
        "created": pd.to_datetime(["2024-11-20", "2024-11-21", "2024-11-22", "2024-11-23"]),
        "day": ["2024-11-20", "2024-11-21", "2024-11-22", "2024-11-23"],
        "target": [1, 0, 0, 1],
        "predict_type": ["TXT_2_IMG", "TXT_2_IMG", "IMG_2_IMG", "IMG_2_IMG"],
        "checkpoint_model_version_id": ["M0", "M0", "M1", "M1"],
        "groupId": ["G0", "G1", "G2", "G3"],
        "prompt_length": [10.0] * 4,
        "negative_prompt_length": [0.0] * 4,
        "negative_prompt_missing": [1] * 4,
        "num_images_per_prompt": [1.0] * 4,
        "num_inference_steps": [30.0] * 4,
        "num_lora": [0.0] * 4,
    })


class RequestFailureStudyTest(unittest.TestCase):
    def test_loader_stops_before_first_test_status_and_ignores_unfinalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "requests.csv"
            rows = [
                request_row("2024-11-28 23:59:59", "FAILED"),
                request_row("2024-11-29 00:00:00", "PENDING"),
                request_row("2024-12-03 23:59:59", "SUCCEED", negative=""),
                # A malformed test outcome must not be inspected by select.
                request_row("2024-12-04 00:00:00", "UNKNOWN", negative="bad"),
            ]
            write_csv(path, rows)
            loaded = MODULE.load_pretest(path)
        self.assertEqual(loaded.target.tolist(), [1, 0])
        self.assertEqual(loaded.source_row.tolist(), [2, 4])
        self.assertEqual(loaded.negative_prompt_missing.tolist(), [0, 1])
        self.assertEqual(loaded.attrs["excluded_unfinalized_status"],
                         {"PENDING": 1, "PROCESSING": 0})
        train, valid = MODULE.split_rows(loaded)
        self.assertEqual((len(train), len(valid)), (1, 1))

    def test_loader_rejects_pretest_bad_inputs_and_time_reversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "requests.csv"
            write_csv(path, [request_row("2024-11-20", "FAILED", negative="nan")])
            with self.assertRaisesRegex(ValueError, "invalid negative_prompt_length"):
                MODULE.load_pretest(path)
            write_csv(path, [request_row("2024-11-21", "FAILED"),
                             request_row("2024-11-20", "SUCCEED")])
            with self.assertRaisesRegex(ValueError, "not monotone"):
                MODULE.load_pretest(path)

    def test_baselines_use_training_only_and_fallback_for_unseen_model(self):
        train = frame()
        valid = frame().iloc[[0, 2]].copy()
        valid.loc[valid.index[1], "checkpoint_model_version_id"] = "M_NEW"
        first = MODULE.baseline_predictions(train, valid)
        changed = valid.copy()
        changed["target"] = 1 - changed["target"]
        second = MODULE.baseline_predictions(train, changed)
        for name in MODULE.BASELINE_ORDER:
            np.testing.assert_allclose(first[name], second[name])
        self.assertEqual(first["model"][1], first["global"][1])
        self.assertTrue(np.all((first["model_type"] > 0) & (first["model_type"] < 1)))

    def test_features_exclude_outcomes_and_group_id(self):
        data = frame()
        before = MODULE.feature_frame(data)
        changed = data.copy()
        changed["target"] = 1 - changed["target"]
        changed["groupId"] = "DIFFERENT"
        changed["exec_time_seconds"] = 99999
        pd.testing.assert_frame_equal(before, MODULE.feature_frame(changed))
        self.assertNotIn("groupId", before.columns)
        self.assertNotIn("target", before.columns)

    def test_candidate_predicts_unseen_model_without_outcome_feature(self):
        train = frame()
        model = MODULE.fit_candidate(train)
        future = train.iloc[[0]].copy()
        future["checkpoint_model_version_id"] = "M_NEW"
        future["predict_type"] = "NEW_TYPE"
        probability = model.predict_proba(MODULE.feature_frame(future))[0, 1]
        self.assertTrue(np.isfinite(probability))
        self.assertGreater(probability, 0)
        self.assertLess(probability, 1)

    def test_day_equal_score_and_baseline_tie_order(self):
        data = frame().iloc[[0, 1, 2]].copy()
        data.loc[data.index[0:2], "day"] = "2024-11-20"
        probabilities = np.array([0.8, 0.2, 0.1])
        result = MODULE.score(data, probabilities)
        expected = (((1 - .8) ** 2 + (0 - .2) ** 2) / 2 + (0 - .1) ** 2) / 2
        self.assertAlmostEqual(result["day_equal_brier"], expected)
        self.assertEqual(result["days"]["2024-11-22"]["average_precision"], None)
        scores = {name: {"day_equal_brier": 0.1} for name in MODULE.BASELINE_ORDER}
        self.assertEqual(MODULE.select_baseline(scores), "global")

    def test_adoption_gate_requires_all_five_frozen_dates(self):
        valid = frame()
        probabilities = np.array([0.8, 0.2, 0.1, 0.9])
        scored = MODULE.score(valid, probabilities)
        with self.assertRaisesRegex(ValueError, "five frozen validation dates"):
            MODULE.adoption_gate(valid, scored, scored, probabilities, probabilities,
                                 np.array([True] * len(valid)))

    def test_group_familiarity_sensitivity_has_separate_scores(self):
        valid = frame()
        candidate = np.array([0.8, 0.2, 0.1, 0.9])
        baseline = np.array([0.5] * len(valid))
        known = np.array([True, True, False, False])
        result = MODULE.group_familiarity_sensitivity(valid, candidate, baseline, known)
        self.assertEqual(result["seen_group"]["requests"], 2)
        self.assertEqual(result["new_group"]["failed"], 1)
        self.assertEqual(result["new_group"]["groups"], 2)
        self.assertAlmostEqual(result["seen_group"]["candidate_brier"], 0.04)
        self.assertAlmostEqual(result["new_group"]["candidate_brier"], 0.01)
        self.assertAlmostEqual(result["new_group"]["baseline_brier"], 0.25)
        empty = MODULE.group_familiarity_sensitivity(
            valid, candidate, baseline, np.array([True] * len(valid))
        )
        self.assertIsNone(empty["new_group"]["candidate_brier"])

    def test_support_gate_rejects_small_or_concentrated_samples(self):
        tiny = frame()
        with self.assertRaisesRegex(ValueError, "train support gate failed"):
            MODULE.check_support(tiny, tiny)
        data = pd.concat([tiny] * 3000, ignore_index=True)
        data["target"] = 0
        data.loc[:200, "target"] = 1
        data["groupId"] = "ONE_GROUP"
        with self.assertRaisesRegex(ValueError, "train support gate failed"):
            MODULE.check_support(data, tiny)

    def test_freeze_hash_guard_blocks_changed_source_before_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "requests.csv"
            write_csv(source, [request_row("2024-11-20", "FAILED")])
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with mock.patch.object(MODULE, "SOURCE", source), mock.patch.object(MODULE, "SOURCE_SHA256", digest):
                MODULE.freeze(root / "output")
                source.write_text(source.read_text() + "changed\n")
                with self.assertRaisesRegex(ValueError, "hash changed"):
                    MODULE.select(root / "output")


if __name__ == "__main__":
    unittest.main()
