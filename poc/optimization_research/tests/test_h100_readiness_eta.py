"""Meaningful information-boundary and risk-score checks for readiness ETA."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts import evaluate_h100_readiness_eta as study


class H100ReadinessETATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = study.read_trials(study.DEFAULT_INPUT)

    def test_source_grid_and_stage_order_gate(self):
        rows = study.build_stage_rows(self.records)
        self.assertEqual(len(self.records), 200)
        self.assertEqual(len(rows), 1200)
        self.assertEqual(
            {x["model"] for x in rows},
            set(study.MODELS),
        )
        self.assertTrue(all(x["actual_remaining_s"] >= 0 for x in rows))

        changed = copy.deepcopy(self.records)
        changed[0]["t"]["health_ok"] = changed[0]["t"]["run_start"]
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "changed.jsonl"
            source.write_text("\n".join(json.dumps(x) for x in changed) + "\n")
            with patch.object(study, "sha256_file", return_value=study.EXPECTED_SHA256):
                with self.assertRaisesRegex(ValueError, "out-of-order"):
                    study.read_trials(source)

    def test_before_stage_feature_does_not_use_later_markers_or_label(self):
        original = copy.deepcopy(self.records[0])
        changed = copy.deepcopy(original)
        future = datetime.fromisoformat(
            changed["t"]["health_ok"].replace("Z", "+00:00")
        ) + timedelta(seconds=20)
        changed["t"]["health_ok"] = future.isoformat().replace("+00:00", "Z")
        changed["t"]["first_token"] = (future + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        changed["vllm"]["markers"]["engine_ready"]["abs"] = (
            future - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")

        original_rows = study.build_stage_rows([original])
        changed_rows = study.build_stage_rows([changed])
        for stage in ("pull_start", "run_start", "weights_loaded", "compile_done"):
            a = next(x for x in original_rows if x["stage"] == stage)
            b = next(x for x in changed_rows if x["stage"] == stage)
            np.testing.assert_array_equal(
                study.feature_matrix([a], use_cache=False),
                study.feature_matrix([b], use_cache=False),
            )
            np.testing.assert_array_equal(
                study.feature_matrix([a], use_cache=True),
                study.feature_matrix([b], use_cache=True),
            )
            self.assertNotEqual(a["actual_remaining_s"], b["actual_remaining_s"])

    def test_oracle_cache_is_an_explicit_optional_feature(self):
        row = {
            "model_size_b": 4.0,
            "elapsed_s": 10.0,
            "condition": "C0",
        }
        another = dict(row, condition="C4")
        no_cache = study.feature_matrix([row, another], use_cache=False)
        oracle_cache = study.feature_matrix([row, another], use_cache=True)
        np.testing.assert_array_equal(no_cache[0], no_cache[1])
        self.assertFalse(np.array_equal(oracle_cache[0], oracle_cache[1]))

    def test_heldout_model_label_change_cannot_change_its_predictions(self):
        rows = study.build_stage_rows(self.records)

        def donor_only_predict(donor, target, *, use_cache):
            self.assertEqual(len({r["model"] for r in target}), 1)
            heldout = target[0]["model"]
            self.assertNotIn(heldout, {r["model"] for r in donor})
            self.assertEqual(len(donor), 150)
            return np.full(len(target), np.median([r["actual_remaining_s"] for r in donor]))

        with patch.object(study, "quantile_predict", side_effect=donor_only_predict):
            before = study.predict_heldout(rows)
            altered = [dict(row) for row in rows]
            for row in altered:
                if row["model"] == study.MODELS[0]:
                    row["actual_remaining_s"] += 1000
            after = study.predict_heldout(altered)

        frozen_methods = study.METHODS
        for a, b in zip(before, after):
            if a["model"] == study.MODELS[0]:
                self.assertEqual(
                    tuple(a[name] for name in frozen_methods),
                    tuple(b[name] for name in frozen_methods),
                )

    def test_quantile_model_and_pinball_measure_underprediction(self):
        donor = [
            {"model_size_b": 1.0 + i / 10, "elapsed_s": float(i),
             "condition": "C0", "actual_remaining_s": float(10 + i)}
            for i in range(30)
        ]
        target = [{"model_size_b": 2.0, "elapsed_s": 10.0, "condition": "C0"}]
        prediction = study.quantile_predict(donor, target, use_cache=False)
        self.assertEqual(prediction.shape, (1,))
        self.assertGreaterEqual(prediction[0], 0)
        self.assertTrue(np.isfinite(prediction[0]))

        rows = [
            {"actual_remaining_s": 20.0, "estimate": 10.0},
            {"actual_remaining_s": 10.0, "estimate": 20.0},
        ]
        score = study.score(rows, "estimate")
        self.assertAlmostEqual(score["pinball_s"], 5.0)
        self.assertEqual(score["coverage"], 0.5)
        self.assertEqual(score["under_gt5_rate"], 0.5)
        self.assertEqual(score["under_gt10_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
