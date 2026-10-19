"""Time, observability, weighting, and probability checks for MaaS v1."""
from __future__ import annotations

import math
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_maas_sm_observability_v1 as study


def raw_row(label: str, time: int, sm: float, *, gpu: float = 1.5,
            cluster: str | None = "c") -> dict:
    return {"instance_hash": label * 64, "time_s": time,
            "cluster_id": cluster, "gpu_count": gpu,
            "sm_util": sm, "samples": 10.0}


def synthetic_stage() -> pd.DataFrame:
    t = 144 * study.DAY_S + 3_600
    rows = []
    # A known high remains H when another future slot is absent.
    rows += [raw_row("a", t - 600, 7), raw_row("a", t - 300, 8),
             raw_row("a", t, 10), raw_row("a", t + 600, 85)]
    rows += [raw_row("a", t + off, 10)
             for off in (900, 1200, 1500, 1800)]
    # B has all six normal future slots.
    rows += [raw_row("b", t - 600, 7), raw_row("b", t - 300, 8),
             raw_row("b", t, 10)]
    rows += [raw_row("b", t + off, 10) for off in study.FUTURE_OFFSETS]
    # C's only high is duplicate and cannot establish H.
    rows += [raw_row("c", t, 10)]
    rows += [raw_row("c", t + off, 90 if off == 900 else 10)
             for off in study.FUTURE_OFFSETS]
    rows += [raw_row("c", t + 900, 90)]
    # D's only high is on a changed allocation.
    rows += [raw_row("d", t, 10)]
    rows += [raw_row("d", t + off, 90 if off == 1200 else 10,
                     gpu=2.0 if off == 1200 else 1.5)
             for off in study.FUTURE_OFFSETS]
    # E has no future measurements. It still belongs to the decision cohort.
    rows += [raw_row("e", t, 10)]
    # F is too near the stage end, despite normal current usage.
    rows += [raw_row("f", 145 * study.DAY_S - 2_100, 10)]
    return pd.DataFrame(rows)


class MaaSDevelopmentTests(unittest.TestCase):
    def test_stage_labels_ignore_invalid_high_and_future_observability_for_cohort(self):
        raw = synthetic_stage()
        frame, _ = study.construct_stage(raw, "validation")
        t = 144 * study.DAY_S + 3_600
        decisions = frame.loc[frame.decision_s == t + 600].set_index("instance_hash")
        self.assertEqual([int(decisions.loc[ch * 64, "y"]) for ch in "abcde"],
                         [0, 1, 2, 2, 2])
        self.assertFalse((frame.instance_hash == "f" * 64).any())
        self.assertTrue(math.isnan(decisions.loc["e" * 64,
                                                   "sm_change_from_oldest_valid"]))
        self.assertEqual(int(decisions.loc["e" * 64, "oldest_valid"]), 0)
        self.assertEqual(int(decisions.loc["e" * 64, "middle_valid"]), 0)
        self.assertEqual(float(decisions.loc["e" * 64, "sm_mean_valid"]), 10.0)
        self.assertNotIn("cluster_id", study.FEATURES)
        self.assertNotIn("instance_hash", study.FEATURES)
        self.assertFalse(any("mem" in name or "future" in name for name in study.FEATURES))

    def test_future_change_flips_label_without_changing_decision_features(self):
        original = synthetic_stage()
        changed = original.copy()
        t = 144 * study.DAY_S + 3_600
        changed.loc[(changed.instance_hash == "b" * 64)
                    & (changed.time_s == t + 600), "sm_util"] = 85.0
        left, _ = study.construct_stage(original, "validation")
        right, _ = study.construct_stage(changed, "validation")
        selector = lambda f: f.loc[(f.instance_hash == "b" * 64)
                                   & (f.decision_s == t + 600)].iloc[0]
        self.assertEqual(int(selector(left).y), 1)
        self.assertEqual(int(selector(right).y), 0)
        for feature in study.FEATURES:
            self.assertEqual(selector(left)[feature], selector(right)[feature])

    def test_equal_ID_weights_and_second_stage_recompute(self):
        ids = np.array(["a", "a", "a", "b"])
        weights = study.equal_id_weights(ids)
        self.assertAlmostEqual(weights[:3].sum(), 2.0)
        self.assertAlmostEqual(weights[3], 2.0)
        kept = np.array([True, True, False, True])
        conditional = study.equal_id_weights(ids[kept])
        self.assertAlmostEqual(conditional[:2].sum(), 1.5)
        self.assertAlmostEqual(conditional[-1], 1.5)

    def test_binning_bounds_temperature_and_tied_ECE(self):
        frame = pd.DataFrame({"sm_current": [0.0, 5.0, 20.0, 50.0, 79.9],
                              "oldest_valid": [0, 1, 1, 1, 1],
                              "middle_valid": [0, 0, 1, 1, 1],
                              "gpu_count": [1.0, 1.5, 4.0, 4.5, .5]})
        self.assertEqual(len(set(study._conditional_cells(frame).tolist())), 5)
        probability = np.array([[.1, .7, .2], [.6, .2, .2]])
        np.testing.assert_allclose(study.temperature_transform(probability, 1.0),
                                   probability)
        self.assertAlmostEqual(study._weighted_ece(
            np.array([0., 1.]), np.array([.2, .2]), np.ones(2))[0], .3)
        self.assertEqual(study._weighted_ece(
            np.array([0., 1.]), np.array([.2, .2]), np.ones(2))[1], 1)

    def test_arrow_filter_returns_no_exposed_late_day_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "compact.parquet"
            frame = pd.DataFrame([raw_row("a", 140 * study.DAY_S, 10),
                                  raw_row("b", 146 * study.DAY_S, 90)])
            frame.to_parquet(path, index=False)
            old = study.SOURCE
            try:
                study.SOURCE = path
                selected = study.load_development_only()
            finally:
                study.SOURCE = old
        self.assertEqual(selected.instance_hash.tolist(), ["a" * 64])
        self.assertEqual(selected.time_s.tolist(), [140 * study.DAY_S])

    def test_unresolved_binary_difference_pairs_the_same_completion(self):
        frame = pd.DataFrame({"instance_hash": ["a", "b", "c"],
                              "y": [0, 1, 2]})
        proposed = np.array([[.6, .3, .1], [.1, .8, .1], [.6, .2, .2]])
        baseline = np.array([[.5, .4, .1], [.2, .7, .1], [.2, .6, .2]])
        bounds = study.binary_difference_bounds(frame, proposed, baseline)
        # The U row has p=.7 and b=.3. Its paired differences are
        # -.4 for the same Y*=1 and +.4 for the same Y*=0.
        self.assertAlmostEqual(bounds["best_possible_ID_equal_brier_difference"],
                               (-.08 - .04 - .4) / 3)
        self.assertAlmostEqual(bounds["worst_possible_ID_equal_brier_difference"],
                               (-.08 - .04 + .4) / 3)
        self.assertTrue(bounds["contains_zero"])

    def test_performance_gate_requires_all_four_baselines_and_three_blocks(self):
        def metric(brier=1., row=1., logloss=1., ece_h=.02, ece_u=.04):
            return {"ID_equal_brier": brier, "row_equal_brier": row,
                    "ID_equal_logloss": logloss,
                    "one_vs_rest": {
                        "H": {"ID_equal_brier": 1.,
                              "ID_equal_ECE_10_equal_weight_bins": ece_h},
                        "U": {"ID_equal_brier": 1.,
                              "ID_equal_ECE_10_equal_weight_bins": ece_u}}}

        scored = {"methods": {"two_stage_hgb": metric(.9, .95, 1.005),
                              **{name: metric() for name in study.BASELINES}},
                  "six_hour_blocks": {
                      str(block): {"two_stage_hgb": metric(.9),
                                   **{name: metric() for name in study.BASELINES}}
                      for block in range(4)}}
        self.assertTrue(study.performance_gate(scored)["all_passed"])
        scored["methods"]["direct_hgb"]["ID_equal_brier"] = .94
        failed = study.performance_gate(scored)
        self.assertFalse(failed["all_passed"])
        self.assertIn("ID_brier_5pct_below_direct_hgb", failed["failed"])
        scored["methods"]["direct_hgb"]["ID_equal_brier"] = 1.
        for block in (0, 3):
            scored["six_hour_blocks"][str(block)]["two_stage_hgb"]["ID_equal_brier"] = 1.1
        failed = study.performance_gate(scored)
        self.assertEqual(failed["six_hour_block_wins"], 2)
        self.assertIn("three_of_four_six_hour_block_wins", failed["failed"])

    def test_atomic_release_links_match_published_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = [root / name for name in ("training.json", "baseline.json",
                                               "calibration.json")]
            for index, path in enumerate(files):
                study.atomic_write_json(path, {"index": index})
                self.assertEqual(json.loads(path.read_text())["index"], index)
                self.assertFalse(list(root.glob("*.tmp")))
            model_path = root / "model.joblib"
            study.atomic_dump_model(model_path, {"constant": np.array([1., 2.])})
            model_hashes = {"artifacts/model.joblib": study.sha256_file(model_path)}
            links = study.release_links(files[0], files[1], files[2], model_hashes)
            self.assertEqual(links["training_result_sha256"], study.sha256_file(files[0]))
            self.assertEqual(links["baseline_parameters_sha256"], study.sha256_file(files[1]))
            self.assertEqual(links["calibration_parameters_sha256"], study.sha256_file(files[2]))
            self.assertEqual(links["fitted_models_sha256"], model_hashes)
            self.assertEqual(links["protocol_sha256"], study.EXPECTED_HASHES["protocol"])
            self.assertEqual(links["training_script_sha256"], study.sha256_file(
                Path(study.__file__)))
            self.assertEqual(links["evaluation_script_sha256"],
                             links["training_script_sha256"])
            with self.assertRaises(FileExistsError):
                study.atomic_write_json(files[0], {"index": 99})
            self.assertEqual(json.loads(files[0].read_text())["index"], 0)


if __name__ == "__main__":
    unittest.main()
