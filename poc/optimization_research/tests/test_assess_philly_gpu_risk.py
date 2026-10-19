"""Contract tests for the fixed Philly research risk-signal boundary."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow.compute as pc
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import assess_philly_gpu_risk as risk


EXAMPLE = ROOT / "examples/philly_gpu_risk_request.json"
EXAMPLE_RESULT = ROOT / "examples/philly_gpu_risk_example_result.json"
CANDIDATES = ROOT / "artifacts/philly_machine_holdout_candidates_v1/candidates.parquet"
PREDICTIONS = ROOT / "artifacts/philly_machine_holdout_score_v1/test_predictions.parquet"


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


class PhillyRiskSignalTests(unittest.TestCase):
    def test_exact_parity_with_saved_holdout_candidate_and_prediction(self):
        request = example()
        candidate = pq.ParquetFile(CANDIDATES).read_row_group(8).slice(0, 1).to_pylist()[0]
        self.assertEqual(candidate["cohort"], "complete")
        self.assertEqual(candidate["current_state"], "below_80")
        self.assertEqual(candidate["expected_gpu_slots"], request["expected_gpu_slots"])
        self.assertEqual(candidate["machine_hash"], request["machine_hash"])
        self.assertEqual(candidate["gpu_index"], request["gpu_index"])
        self.assertEqual(candidate["decision_utc_s"], request["decision_utc_s"])
        for role, column in zip(risk.ROLES, ("older_util", "middle_util", "current_util")):
            window = next(item for item in request["windows"] if item["role"] == role)
            self.assertEqual(window["mean_util_percent"], candidate[column])
        self.assertTrue(candidate["older_valid"] and candidate["middle_valid"])
        self.assertEqual(candidate["past_gap_s"], 300)

        saved = pq.read_table(PREDICTIONS, columns=[
            "machine_hash", "gpu_index", "decision_utc_s", "cohort",
            "current_state", "pooled_quality", "selected_beta"])
        matched = saved.filter(pc.and_(
            pc.and_(pc.equal(saved["machine_hash"], request["machine_hash"]),
                    pc.equal(saved["gpu_index"], request["gpu_index"])),
            pc.equal(saved["decision_utc_s"], request["decision_utc_s"]))).to_pylist()
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["cohort"], "complete")
        self.assertEqual(matched[0]["current_state"], "below_80")
        result = risk.assess_philly_gpu_risk(request)
        self.assertEqual(result["status"], "RESEARCH_ESTIMATE")
        self.assertIs(result["input_observation_verified"], False)
        self.assertEqual(result, json.loads(EXAMPLE_RESULT.read_text(encoding="utf-8")))
        self.assertEqual(result["assessed_request_sha256"], hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest())
        self.assertEqual(result["pooled_probability"], matched[0]["pooled_quality"])
        self.assertEqual(result["selected_beta_probability"], matched[0]["selected_beta"])
        self.assertFalse(result["scope"]["deployment_authority"])
        self.assertNotIn("action", result)
        self.assertNotIn("vm", result)

    def test_invalid_and_out_of_scope_requests_abstain_without_prediction(self):
        changes = [
            (lambda q: q.update(source_domain="other"), "unsupported_source_domain"),
            (lambda q: q.update(expected_gpu_slots=1), "unsupported_expected_gpu_slots"),
            (lambda q: q.update(gpu_index=True), "invalid_gpu_index"),
            (lambda q: q.update(decision_utc_s=q["decision_utc_s"] + 1), "invalid_decision_utc_s"),
            (lambda q: q["windows"].reverse(), "window_0_role_or_order_mismatch"),
            (lambda q: q["windows"][0].update(gpu_index=1), "window_0_different_gpu_slot"),
            (lambda q: q["windows"][1].update(window_start_utc_s=q["decision_utc_s"] - 1200),
             "window_1_wrong_start_time"),
            (lambda q: q["windows"][2].update(available_at_utc_s=q["decision_utc_s"] + 1),
             "window_2_unavailable_at_decision"),
            (lambda q: q["windows"][0].update(available_at_utc_s=q["decision_utc_s"] - 1201),
             "window_0_unavailable_at_decision"),
            (lambda q: q["windows"][1].update(valid_minutes=4), "window_1_incomplete"),
            (lambda q: q["windows"][0].update(mean_util_percent=101),
             "window_0_invalid_utilization"),
            (lambda q: q["windows"][0].update(mean_util_percent=10 ** 1000),
             "window_0_invalid_utilization"),
            (lambda q: q["windows"][2].update(mean_util_percent=80),
             "current_utilization_at_least_80"),
            (lambda q: q.update(future_util_percent=100), "request_fields_mismatch"),
        ]
        with patch.object(risk.holdout.FrozenOriginalModel, "predict_pooled",
                          side_effect=AssertionError("invalid request reached model")):
            for change, reason in changes:
                with self.subTest(reason=reason):
                    request = copy.deepcopy(example())
                    change(request)
                    result = risk.assess_philly_gpu_risk(request)
                    self.assertEqual(result["status"], "ABSTAIN")
                    self.assertEqual(result["assessed_request_sha256"],
                                     risk.canonical_request_sha256(request))
                    self.assertIs(result["input_observation_verified"], False)
                    self.assertIn(reason, result["reasons"])
                    self.assertIsNone(result["pooled_probability"])
                    self.assertIsNone(result["selected_beta_probability"])

    def test_model_source_and_code_hash_fail_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "changed"
            bad.write_bytes(b"changed")
            with patch.object(risk.holdout, "load_frozen_model_spec",
                              side_effect=AssertionError("hash failure reached model")):
                with self.assertRaisesRegex(ValueError, "model_spec_sha256_mismatch"):
                    risk.assess_philly_gpu_risk(example(), spec_path=bad)
                with self.assertRaisesRegex(ValueError, "normalized_source_sha256_mismatch"):
                    risk.assess_philly_gpu_risk(example(), source_path=bad)
                paths = dict(risk.CODE_PATHS)
                paths["risk_models"] = bad
                with self.assertRaisesRegex(ValueError, "risk_models_sha256_mismatch"):
                    risk.assess_philly_gpu_risk(example(), code_paths=paths)

    def test_cli_prints_risk_only_and_malformed_input_abstains(self):
        script = ROOT / "scripts/assess_philly_gpu_risk.py"
        completed = subprocess.run([sys.executable, str(script), "--input", str(EXAMPLE)],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "RESEARCH_ESTIMATE")
        self.assertIs(result["input_observation_verified"], False)
        self.assertFalse(result["scope"]["deployment_authority"])
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"
            invalid = example()
            invalid["windows"][2]["valid_minutes"] = 0
            bad.write_text(json.dumps(invalid), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(script), "--input", str(bad)],
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "ABSTAIN")
            self.assertIn("window_2_incomplete", result["reasons"])


if __name__ == "__main__":
    unittest.main()
