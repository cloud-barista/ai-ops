"""Offline mixed-ISO timestamp and pre-score failure checks for v1.1."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_azure_input_demand_cross_service_v1_1 as study


class AzureConversationParserRepairTests(unittest.TestCase):
    def test_mixed_fractional_and_whole_second_iso8601_at_five_minute_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mixed.csv"
            path.write_text(
                "TIMESTAMP,ContextTokens,GeneratedTokens\n"
                "2024-05-12 00:00:00.001+00:00,10,SECRET_A\n"
                "2024-05-12 00:00:01+00:00,20,SECRET_B\n"
                "2024-05-12 00:04:59.999999+00:00,30,SECRET_C\n"
                "2024-05-12 00:05:00+00:00,40,SECRET_D\n"
                "2024-05-18 23:59:59.995460+00:00,50,SECRET_E\n")
            y, counts, nrows = study.aggregate_source_iso(path)
            self.assertEqual(nrows, 5)
            self.assertEqual(y[0], 60)
            self.assertEqual(counts[0], 3)
            self.assertEqual(y[1], 40)
            self.assertEqual(counts[1], 1)
            self.assertEqual(y[-1], 50)

    def test_non_parser_source_invariants_still_reject_bad_rows(self):
        cases = (
            ["2024-05-12 00:05:00.001+00:00,10", "2024-05-12 00:00:00+00:00,20"],
            ["2024-05-11 23:59:59+00:00,10"],
            ["2024-05-19 00:00:00+00:00,10"],
            ["2024-05-12 00:00:00+00:00,0"],
            ["2024-05-12 00:00:00+00:00,-1"],
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.csv"
            for rows in cases:
                path.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                                + "\n".join(f"{row},SECRET" for row in rows) + "\n")
                with self.assertRaises(ValueError):
                    study.aggregate_source_iso(path)

    def test_pinned_v1_failure_and_manifest_digest_gate(self):
        failure = study._failure_record()
        self.assertFalse(failure["model_loaded"])
        self.assertFalse(failure["scores_computed"])
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / "release.json"
            release.write_text('{"status":"synthetic"}\n')
            with mock.patch.object(study, "RELEASE", release):
                with self.assertRaisesRegex(ValueError, "changed after review"):
                    study.verify_release("0" * 64)

    def test_new_parser_error_is_recorded_without_model_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.csv"
            source.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                              "2024-05-12 00:00:00+00:00,10,SECRET\n")
            paths = self._temporary_paths(root)
            with ExitStack() as stack:
                for patcher in paths:
                    stack.enter_context(patcher)
                with (mock.patch.object(study, "verify_release",
                                        return_value=("r" * 64, {})),
                      mock.patch.object(study.v1, "SOURCE", source),
                      mock.patch.object(study, "aggregate_source_iso",
                                        side_effect=ValueError("synthetic parser error")),
                      mock.patch.object(study.joblib, "load",
                                        side_effect=AssertionError("model loaded"))):
                    result = study.score_once("r" * 64)
            self.assertEqual(result["status"], "CONVERSATION_SOURCE_NO_GO_NO_MODEL_SCORE")
            self.assertFalse((root / "started.json").exists())
            self.assertTrue((root / "source_failure.json").exists())

    def test_support_failure_is_before_model_load_and_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.csv"
            source.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                              "2024-05-12 00:00:00+00:00,10,SECRET\n")
            y = np.full(study.coding.N_SLOTS, 2_000., dtype=float)
            counts = np.ones(study.coding.N_SLOTS, dtype=np.int64)
            frame = study.coding.design_from_aggregates(y, start=study.v1.CONV_START)
            frame.loc[2 * 288:2 * 288 + 14, "scale"] = 999.
            paths = self._temporary_paths(root)
            with ExitStack() as stack:
                for patcher in paths:
                    stack.enter_context(patcher)
                with (mock.patch.object(study, "verify_release",
                                        return_value=("r" * 64, {})),
                      mock.patch.object(study.v1, "SOURCE", source),
                      mock.patch.object(study, "aggregate_source_iso",
                                        return_value=(y, counts, int(counts.sum()))),
                      mock.patch.object(study.coding, "design_from_aggregates",
                                        return_value=frame),
                      mock.patch.object(study.joblib, "load",
                                        side_effect=AssertionError("model loaded")),
                      mock.patch.object(study.coding, "score_period",
                                        side_effect=AssertionError("score calculated"))):
                    result = study.score_once("r" * 64)
            self.assertEqual(result["status"], "CONVERSATION_SUPPORT_NO_GO_NO_MODEL_SCORE")
            self.assertFalse((root / "started.json").exists())
            self.assertTrue((root / "support_failure.json").exists())

    @staticmethod
    def _temporary_paths(root: Path) -> list:
        return [mock.patch.object(study, name, root / filename)
                for name, filename in (
                    ("RESULT", "result.json"), ("STARTED", "started.json"),
                    ("SUPPORT_FAILURE", "support_failure.json"),
                    ("SOURCE_FAILURE", "source_failure.json"),
                    ("SCORING_FAILURE", "scoring_failure.json"))]


if __name__ == "__main__":
    unittest.main()
