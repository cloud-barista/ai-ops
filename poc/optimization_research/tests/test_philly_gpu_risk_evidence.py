"""Versioned, request-bound Philly risk evidence and one-step generation."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import assess_philly_gpu_risk as risk
from emit_philly_gpu_risk_evidence import (
    SCHEMA_PATH, _canonical_bytes, build_evidence,
)
from generate_philly_gpu_risk_evidence import generate_philly_gpu_risk_evidence


REQUEST = ROOT / "examples/philly_gpu_risk_request.json"
RESULT = ROOT / "examples/philly_gpu_risk_example_result.json"
EXAMPLE = ROOT / "examples/philly_gpu_risk_evidence_example.json"
SCRIPT = ROOT / "scripts/generate_philly_gpu_risk_evidence.py"


def example_pair() -> tuple[dict, dict]:
    return (json.loads(REQUEST.read_text(encoding="utf-8")),
            json.loads(RESULT.read_text(encoding="utf-8")))


class PhillyRiskEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(cls.schema)
        cls.validator = Draft7Validator(cls.schema, format_checker=FormatChecker())

    def test_saved_request_and_result_generate_exact_bound_example(self):
        request, saved_result = example_pair()
        self.assertEqual(risk.assess_philly_gpu_risk(request), saved_result)
        expected = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        actual = generate_philly_gpu_risk_evidence(
            request, generated_at=expected["generated_at"])
        self.assertEqual(actual, expected)
        self.assertEqual(list(self.validator.iter_errors(actual)), [])
        self.assertEqual(actual["request_sha256"], hashlib.sha256(
            _canonical_bytes(request)).hexdigest())
        self.assertEqual(actual["evidence_id"], "sha256:" + hashlib.sha256(
            _canonical_bytes({"request_sha256": actual["request_sha256"],
                              "result": saved_result})).hexdigest())
        self.assertEqual(actual["pooled_probability"],
                         saved_result["pooled_probability"])
        self.assertEqual(actual["selected_beta_probability"],
                         saved_result["selected_beta_probability"])
        self.assertEqual(actual["decision_context"]["decision_utc_s"],
                         request["decision_utc_s"])
        self.assertEqual(actual["decision_context"]["expected_gpu_slots"],
                         request["expected_gpu_slots"])
        self.assertIs(actual["input_observation_verified"], False)
        self.assertIs(actual["scope"]["deployment_authority"], False)
        for forbidden in ("action", "desired_infrastructure", "node_count",
                          "vm_id", "resource_recommendation"):
            self.assertNotIn(forbidden, json.dumps(actual).lower())
        later = build_evidence(request, saved_result,
                               generated_at="2026-09-28T22:25:31Z")
        self.assertEqual(later["evidence_id"], actual["evidence_id"])

    def test_invalid_request_yields_bound_abstain_without_probability(self):
        request, _ = example_pair()
        request["windows"][2]["valid_minutes"] = 4
        expected = generate_philly_gpu_risk_evidence(
            request, generated_at="2026-09-28T22:25:30Z")
        self.assertEqual(expected["status"], "ABSTAIN")
        self.assertEqual(expected["reasons"], ["window_2_incomplete"])
        self.assertIsNone(expected["pooled_probability"])
        self.assertIsNone(expected["selected_beta_probability"])
        self.assertEqual(expected["decision_context"], {
            "decision_utc_s": request["decision_utc_s"],
            "machine_hash": request["machine_hash"],
            "gpu_index": request["gpu_index"],
            "expected_gpu_slots": request["expected_gpu_slots"],
        })
        self.assertEqual(expected["request_sha256"], hashlib.sha256(
            _canonical_bytes(request)).hexdigest())
        self.assertEqual(list(self.validator.iter_errors(expected)), [])
        other = copy.deepcopy(request)
        other["source_domain"] = "not_philly"
        changed = generate_philly_gpu_risk_evidence(
            other, generated_at="2026-09-28T22:25:30Z")
        self.assertIn("unsupported_source_domain", changed["reasons"])
        self.assertNotIn("decision_context", changed)
        self.assertNotEqual(changed["request_sha256"], expected["request_sha256"])
        self.assertNotEqual(changed["evidence_id"], expected["evidence_id"])
        invalid_identity = copy.deepcopy(request)
        invalid_identity["gpu_index"] = True
        malformed = generate_philly_gpu_risk_evidence(
            invalid_identity, generated_at="2026-09-28T22:25:30Z")
        self.assertEqual(malformed["status"], "ABSTAIN")
        self.assertIn("invalid_gpu_index", malformed["reasons"])
        self.assertNotIn("decision_context", malformed)

    def test_rejects_malformed_estimates_identity_and_provenance(self):
        request, saved = example_pair()
        cases = [
            (lambda result: result.update(pooled_probability=float("nan")),
             "non_json_or_nonfinite_input"),
            (lambda result: result.update(pooled_probability=-0.01),
             "invalid_risk_probability"),
            (lambda result: result.update(selected_beta_probability=1.01),
             "invalid_risk_probability"),
            (lambda result: result.update(pooled_probability=True),
             "invalid_risk_probability"),
            (lambda result: result.update(machine_hash="f" * 64),
             "assessor_decision_identity_mismatch"),
            (lambda result: result.update(gpu_index=True),
             "assessor_decision_identity_mismatch"),
            (lambda result: result.update(decision_utc_s=request["decision_utc_s"] + 300),
             "assessor_decision_identity_mismatch"),
            (lambda result: result.update(horizon_seconds=1800.0),
             "unsupported_assessor_result"),
            (lambda result: result["scope"].update(deployment_authority=True),
             "unsupported_assessor_result"),
            (lambda result: result.update(input_observation_verified=True),
             "unsupported_assessor_result"),
            (lambda result: result["provenance"].update(
                artifact_hashes_verified_at_load=False),
             "unexpected_assessor_provenance"),
            (lambda result: result.update(action="SCALE_OUT"),
             "estimate_result_or_request_mismatch"),
            (lambda result: result.update(assessed_request_sha256="0" * 64),
             "assessor_request_digest_mismatch"),
        ]
        for change, reason in cases:
            with self.subTest(reason=reason, change=str(change)):
                altered = copy.deepcopy(saved)
                change(altered)
                with self.assertRaisesRegex(ValueError, reason):
                    build_evidence(request, altered,
                                   generated_at="2026-09-28T22:25:30Z")
        changed_request = copy.deepcopy(request)
        changed_request["windows"][0]["mean_util_percent"] = 20.0
        with self.assertRaisesRegex(ValueError, "assessor_request_digest_mismatch"):
            build_evidence(changed_request, saved,
                           generated_at="2026-09-28T22:25:30Z")
        changed_request = copy.deepcopy(request)
        changed_request["machine_hash"] = "f" * 64
        for window in changed_request["windows"]:
            window["machine_hash"] = changed_request["machine_hash"]
        with self.assertRaisesRegex(ValueError, "assessor_request_digest_mismatch"):
            build_evidence(changed_request, saved,
                           generated_at="2026-09-28T22:25:30Z")

    def test_rejects_forged_abstain_and_invalid_generation_time(self):
        request, saved = example_pair()
        invalid = copy.deepcopy(request)
        invalid["windows"][1]["valid_minutes"] = 0
        result = risk.assess_philly_gpu_risk(invalid)
        self.assertEqual(result["status"], "ABSTAIN")
        altered = copy.deepcopy(result)
        altered["reasons"] = ["other_reason"]
        with self.assertRaisesRegex(ValueError, "abstain_result_or_request_mismatch"):
            build_evidence(invalid, altered)
        altered = copy.deepcopy(result)
        altered["pooled_probability"] = 0.5
        with self.assertRaisesRegex(ValueError, "abstain_result_or_request_mismatch"):
            build_evidence(invalid, altered)
        with self.assertRaisesRegex(ValueError, "assessor_request_digest_mismatch"):
            build_evidence(request, result)
        with self.assertRaisesRegex(ValueError, "generated_at_must_be_rfc3339_utc_z"):
            build_evidence(request, saved, generated_at="2026-09-29T00:25:30+02:00")
        with self.assertRaisesRegex(ValueError, "evidence_generated_before_decision"):
            build_evidence(request, saved, generated_at="2017-11-30T23:59:59Z")
        with self.assertRaisesRegex(ValueError, "evidence_generated_before_decision"):
            build_evidence(invalid, result, generated_at="2017-11-30T23:59:59Z")

    def test_schema_rejects_action_and_status_probability_mismatch(self):
        evidence = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        evidence["action"] = "SCALE_OUT"
        self.assertTrue(list(self.validator.iter_errors(evidence)))
        del evidence["action"]
        evidence["status"] = "ABSTAIN"
        evidence["reasons"] = ["invalid_window"]
        self.assertTrue(list(self.validator.iter_errors(evidence)))

    def test_cli_atomic_output_hash_failure_and_collision(self):
        expected = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence.json"
            base = [sys.executable, str(SCRIPT), "--request", str(REQUEST),
                    "--generated-at", expected["generated_at"]]
            completed = subprocess.run(
                base + ["--output", str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             expected)
            self.assertEqual(list(root.glob("*.tmp")), [])
            collision = subprocess.run(
                base + ["--output", str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(collision.returncode, 2)
            self.assertIn("output_path_already_exists", collision.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             expected)
            bad_model = root / "changed.json"
            bad_model.write_text("{}", encoding="utf-8")
            absent = root / "never_written.json"
            failed = subprocess.run(
                base + ["--model-spec", str(bad_model), "--output", str(absent)],
                capture_output=True, text=True, check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertIn("model_spec_sha256_mismatch", failed.stderr)
            self.assertFalse(absent.exists())
            invalid = json.loads(REQUEST.read_text(encoding="utf-8"))
            invalid["windows"][2]["valid_minutes"] = 4
            invalid_path = root / "invalid_request.json"
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            abstain_path = root / "abstain.json"
            abstained = subprocess.run(
                [sys.executable, str(SCRIPT), "--request", str(invalid_path),
                 "--generated-at", expected["generated_at"],
                 "--output", str(abstain_path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(abstained.returncode, 0, abstained.stderr)
            abstain = json.loads(abstain_path.read_text(encoding="utf-8"))
            self.assertEqual(abstain["status"], "ABSTAIN")
            self.assertEqual(abstain["reasons"], ["window_2_incomplete"])
            self.assertEqual(abstain["decision_context"]["decision_utc_s"],
                             invalid["decision_utc_s"])
            self.assertEqual(list(self.validator.iter_errors(abstain)), [])


if __name__ == "__main__":
    unittest.main()
