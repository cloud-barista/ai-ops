"""Small, synthetic checks for the frozen SwissAI follow-up path."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_swissai_idle_return as original  # noqa: E402
import evaluate_swissai_idle_return_post as followup  # noqa: E402


class SwissAIIdleReturnPostTests(unittest.TestCase):
    def test_post_generator_matches_frozen_generator_on_old_interval(self) -> None:
        start = int(original.SPLIT_STARTS[2])
        end = int(original.SPLIT_ENDS[2])
        a = np.array([start - 5 * original.MIN_US, start + 8 * original.MIN_US,
                      start + 18 * original.MIN_US, start + 40 * original.MIN_US,
                      end - 12 * original.MIN_US, end - original.MIN_US], dtype=np.int64)
        b = np.array([start + original.MIN_US, start + 13 * original.MIN_US,
                      start + 24 * original.MIN_US, start + 50 * original.MIN_US], dtype=np.int64)
        trace = original.Trace(np.sort(np.r_[a, b]), {"a": a, "b": b}, {})
        expected = original.build_episodes(trace, include_test_labels=True)
        expected = expected[expected.split == 2].drop(columns="split")
        expected = expected.sort_values(["model_alias", "t_us"]).reset_index(drop=True)
        with patch.object(followup, "POST_START", start), patch.object(followup, "POST_END", end):
            actual = followup.build_post_episodes(trace).drop(columns="split")
        pd.testing.assert_frame_equal(actual, expected, check_like=True)
        self.assertTrue((actual.t_us + original.TARGET_US <= end).all())

    def test_history_maturity_is_strict_and_order_independent(self) -> None:
        base = int(original.SPLIT_STARTS[0])
        minute = original.MIN_US
        rows = pd.DataFrame({
            "model_alias": ["a"] * 4,
            "t_us": [base, base + 10 * minute, base + 10 * minute + 1, base + 25 * minute],
            "y": [1, 0, 1, 0],
        })
        for perm in ([0, 1, 2, 3], [3, 1, 0, 2]):
            candidate = rows.iloc[perm].reset_index(drop=True)
            n, a = original._history(candidate)
            found = {int(t): (int(count), int(positive))
                     for t, count, positive in zip(candidate.t_us, n, a)}
            self.assertEqual(found[base], (0, 0))
            self.assertEqual(found[base + 10 * minute], (0, 0))  # Equality is excluded.
            self.assertEqual(found[base + 10 * minute + 1], (1, 1))
            self.assertEqual(found[base + 25 * minute], (3, 2))
            p = np.array([0.1, 0.2, 0.3, 0.4])
            mixed, empirical = followup.fixed_blend(p, n, a)
            self.assertAlmostEqual(mixed[list(candidate.t_us).index(base)],
                                   p[list(candidate.t_us).index(base)])
            self.assertAlmostEqual(empirical[list(candidate.t_us).index(base)], followup.PRIOR)

    def test_fixed_blend_large_history_and_input_guards(self) -> None:
        mixed, empirical = followup.fixed_blend(
            np.array([0.2]), np.array([1000]), np.array([700]))
        self.assertAlmostEqual(float(mixed[0]), 720 / 1100)
        self.assertAlmostEqual(float(empirical[0]), (700 + 100 * followup.PRIOR) / 1100)
        with self.assertRaisesRegex(ValueError, "Invalid fixed-model probabilities"):
            followup.fixed_blend(np.array([float("nan")]), np.array([0]), np.array([0]))
        with self.assertRaisesRegex(ValueError, "Invalid completed-history counts"):
            followup.fixed_blend(np.array([0.5]), np.array([1]), np.array([2]))

    def test_literal_hash_version_and_overwrite_guards(self) -> None:
        self.assertEqual(followup._check_fixed_inputs()["selected_model"], "logistic")
        with tempfile.TemporaryDirectory() as directory:
            wrong_model = Path(directory) / "wrong.joblib"
            wrong_model.write_bytes(b"not a saved model")
            with patch.object(followup, "MODEL", wrong_model):
                with self.assertRaisesRegex(ValueError, "selected model SHA-256"):
                    followup._check_fixed_inputs()
            wrong_spec = Path(directory) / "model_spec.json"
            wrong_spec.write_text(json.dumps({"model_sha256": "forged"}))
            with patch.object(followup, "MODEL_SPEC", wrong_spec):
                with self.assertRaisesRegex(ValueError, "specification SHA-256"):
                    followup._check_fixed_inputs()
            with patch.object(followup.sklearn, "__version__", "0.0.0"):
                with self.assertRaisesRegex(ValueError, "scikit-learn version"):
                    followup._check_fixed_inputs()
            with patch.object(followup.np, "__version__", "0.0.0"):
                with self.assertRaisesRegex(ValueError, "NumPy version"):
                    followup._check_fixed_inputs()
            with patch.object(followup.pd, "__version__", "0.0.0"):
                with self.assertRaisesRegex(ValueError, "pandas version"):
                    followup._check_fixed_inputs()
            existing = Path(directory) / "exists.json"
            existing.write_text("already here")
            with self.assertRaises(FileExistsError):
                followup._refuse_existing(existing)
            with self.assertRaisesRegex(ValueError, "64-hex"):
                followup.score(expected_manifest_sha256="bad", output_dir=Path(directory))

    def test_freeze_does_not_materialize_post_episodes(self) -> None:
        spec = followup._check_fixed_inputs()
        pre = pd.DataFrame({"split": np.array([0, 1, 2]), "y": np.array([1, 0, 1])})
        trace = original.Trace(np.array([0, 1]), {}, spec["source_hashes"])
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "freeze_manifest.json"
            with (patch.object(followup, "_check_fixed_inputs", return_value=spec),
                  patch.object(original, "load_trace", return_value=trace),
                  patch.object(followup, "_pre_episodes", return_value=pre),
                  patch.object(followup, "build_post_episodes",
                               side_effect=AssertionError("post episodes must remain unopened"))):
                digest = followup.freeze(manifest_path=manifest)
            self.assertEqual(digest, followup.sha256(manifest))
            self.assertTrue(manifest.exists())


if __name__ == "__main__":
    unittest.main()
