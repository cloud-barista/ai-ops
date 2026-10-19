"""Offline timing, support, gate, and provenance tests for Azure transfer."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_azure_input_demand_cross_service as study


class AzureConversationTransferTests(unittest.TestCase):
    def test_conversation_utc_grid_uses_only_context_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.csv"
            path.write_text(
                "TIMESTAMP,ContextTokens,GeneratedTokens\n"
                "2024-05-12 00:00:00+00:00,10,SECRET_OUTPUT_A\n"
                "2024-05-12 00:04:59+00:00,20,SECRET_OUTPUT_B\n"
                "2024-05-18 23:59:59+00:00,30,SECRET_OUTPUT_C\n")
            y, counts, nrows = study.coding.aggregate_source(
                path, start=study.CONV_START, n_slots=study.coding.N_SLOTS)
            self.assertEqual(nrows, 3)
            self.assertEqual(y[0], 30)
            self.assertEqual(y[-1], 30)
            self.assertEqual(counts[0], 2)
            self.assertEqual(counts[-1], 1)
            path.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                            "2024-05-19 00:00:00+00:00,10,SECRET\n")
            with self.assertRaisesRegex(ValueError, "outside fixed seven days"):
                study.coding.aggregate_source(path, start=study.CONV_START,
                                              n_slots=study.coding.N_SLOTS)

    def test_utc_start_support_threshold_and_zero_target(self):
        y = np.full(study.coding.N_SLOTS, 2_000., dtype=float)
        y[2 * 288] = 0.
        counts = np.ones(study.coding.N_SLOTS, dtype=np.int64)
        frame = study.coding.design_from_aggregates(y, start=study.CONV_START)
        self.assertEqual(float(frame.loc[2 * 288, "scale"]), 2_000.)
        self.assertEqual(float(frame.loc[2 * 288, "y"]), 0.)
        self.assertEqual(float(frame.loc[12, "weekend"]), 1.)
        self.assertEqual(float(frame.loc[288, "weekend"]), 0.)
        support = study.support_before_score(frame, counts, int(counts.sum()))
        self.assertTrue(support["all_passed"])
        self.assertEqual(support["daily"]["2024-05-14"]["eligible_bins"], 288)
        frame.loc[2 * 288:2 * 288 + 13, "scale"] = 999.
        self.assertTrue(study.support_before_score(frame, counts, int(counts.sum()))[
            "all_passed"])
        frame.loc[2 * 288 + 14, "scale"] = 999.
        failed = study.support_before_score(frame, counts, int(counts.sum()))
        self.assertFalse(failed["all_passed"])
        self.assertEqual(failed["daily"]["2024-05-14"]["ineligible_bins"], 15)

    def test_five_daily_scores_and_transfer_gate(self):
        y = np.full(study.coding.N_SLOTS, 2_000., dtype=float)
        frame = study.coding.design_from_aggregates(y, start=study.CONV_START)
        forecasts = {name: np.full(study.coding.N_SLOTS, 2_000., dtype=float)
                     for name in study.coding.METHODS}
        scored = study.coding.score_period(frame, forecasts, 2, 6,
                                          start=study.CONV_START)
        self.assertEqual(list(scored["per_day"]), [
            "2024-05-14", "2024-05-15", "2024-05-16",
            "2024-05-17", "2024-05-18"])
        self.assertTrue(all(day["eligible_bins"] == 288
                            for day in scored["per_day"].values()))
        self.assertEqual(scored["equal_day_mean_absolute_token_pinball"][
            "relative_q90_hgb"], 0.)
        synthetic = {
            "equal_day_mean_absolute_token_pinball": {
                "relative_q90_hgb": 90.,
                **{name: 100. for name in study.coding.BASELINES}},
            "support_fraction_each_day_at_least_0_95": True,
            "per_day": {str(day): {"methods": {
                "relative_q90_hgb": {"absolute_token_pinball": 90., "coverage": .9},
                **{name: {"absolute_token_pinball": 100., "coverage": .9}
                   for name in study.coding.BASELINES}}}
                for day in range(5)},
        }
        self.assertTrue(study.transfer_gate(synthetic)["all_passed"])
        for day in (0, 1):
            synthetic["per_day"][str(day)]["methods"][
                study.coding.BASELINES[0]]["absolute_token_pinball"] = 80.
        self.assertIn("at_least_four_of_five_daily_wins",
                      study.transfer_gate(synthetic)["failed"])

    def test_support_failure_stops_before_model_load_or_scoring(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.csv"
            source.write_text("TIMESTAMP,ContextTokens,GeneratedTokens\n"
                              "2024-05-12 00:00:00+00:00,10,SECRET\n")
            y = np.full(study.coding.N_SLOTS, 2_000., dtype=float)
            frame = study.coding.design_from_aggregates(y, start=study.CONV_START)
            frame.loc[2 * 288:2 * 288 + 14, "scale"] = 999.
            counts = np.ones(study.coding.N_SLOTS, dtype=np.int64)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with (mock.patch.object(study, "SOURCE", source),
                  mock.patch.object(study, "SOURCE_BYTES", source.stat().st_size),
                  mock.patch.object(study, "SOURCE_SHA256", digest),
                  mock.patch.object(study, "OUTPUT", root),
                  mock.patch.object(study, "RESULT", root / "result.json"),
                  mock.patch.object(study, "verify_release_manifest",
                                    return_value=("r" * 64, {})),
                  mock.patch.object(study.coding, "aggregate_source",
                                    return_value=(y, counts, int(counts.sum()))),
                  mock.patch.object(study.coding, "design_from_aggregates",
                                    return_value=frame),
                  mock.patch.object(study.joblib, "load",
                                    side_effect=AssertionError("model loaded before support")),
                  mock.patch.object(study.coding, "score_period",
                                    side_effect=AssertionError("score before support"))):
                report = study.score_once("r" * 64)
            self.assertEqual(report["status"], "CONVERSATION_SUPPORT_NO_GO_NO_MODEL_SCORE")
            self.assertFalse((root / "conversation_scoring_started.json").exists())
            self.assertTrue((root / "conversation_support_failure.json").exists())

    def test_manifest_and_artifact_sha_refusal_before_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            release = root / "release.json"
            release.write_text('{"status":"fake"}\n')
            artifact = root / "synthetic_test.py"
            artifact.write_text("# frozen\n")
            relative = str(artifact.relative_to(ROOT))
            record = {"path": relative, "sha256": study._sha256(artifact)}
            with (mock.patch.object(study, "RELEASE", release),
                  mock.patch.object(study.urllib.request, "urlopen",
                                    side_effect=AssertionError("network opened"))):
                with self.assertRaisesRegex(ValueError, "changed since independent review"):
                    study.download_conversation("0" * 64)
            with (mock.patch.dict(study.ARTIFACT_PATHS, {"cross_tests": artifact}),
                  mock.patch.dict(study.PINNED_ARTIFACT_HASHES, {}, clear=True)):
                self.assertEqual(study._verify_artifact("cross_tests", record), record["sha256"])
                artifact.write_text("# changed\n")
                with self.assertRaisesRegex(ValueError, "changed after freeze"):
                    study._verify_artifact("cross_tests", record)

    def test_coding_environment_change_blocks_release(self):
        hashes = {
            "coding_source": study.coding.SOURCE_SHA256,
            "coding_protocol": study.CODING_PROTOCOL_SHA256,
            "coding_code": study.CODING_CODE_SHA256,
            "coding_tests": study.CODING_TEST_SHA256,
            "coding_freeze": study._sha256(study.ARTIFACT_PATHS["coding_freeze"]),
            "coding_model": study.CODING_MODEL_SHA256,
            "coding_result": study.CODING_RESULT_SHA256,
        }
        self.assertIn("coding_retrospective_gate_pass",
                      study._check_coding_lineage(hashes))
        changed = {**study.coding.code_environment(), "sklearn": "changed-version"}
        with mock.patch.object(study.coding, "code_environment", return_value=changed):
            with self.assertRaisesRegex(ValueError, "pre-score freeze provenance mismatch"):
                study._check_coding_lineage(hashes)


if __name__ == "__main__":
    unittest.main()
