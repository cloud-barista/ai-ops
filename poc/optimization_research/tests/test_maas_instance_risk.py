"""Causal-window and gate checks for the pre-registered MaaS risk experiment."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_maas_instance_risk.py"
SPEC = importlib.util.spec_from_file_location("evaluate_maas_instance_risk", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def series(instance: str, start: int, count: int, sm: float = 10.0) -> pd.DataFrame:
    rows = []
    instance_hash = hashlib.sha256(instance.encode()).hexdigest()
    for n in range(count):
        t = start + n * 300
        rows.append({
            "instance_hash": instance_hash, "time_s": t, "cluster_id": "cluster-a",
            "gpu_count": 2, "sm_util": sm, "gpu_mem_util": 20.0, "samples": 15,
            "alarm_tie_hash": hashlib.sha256(f"{instance}:{t+600}".encode()).hexdigest(),
        })
    return pd.DataFrame(rows)


def candidate_at(frame: pd.DataFrame, time_s: int) -> pd.Series:
    matches = frame.loc[frame.time_s.eq(time_s)]
    if len(matches) != 1:
        raise AssertionError(f"Expected one eligible candidate at {time_s}; got {len(matches)}")
    return matches.iloc[0]


def valid_audit(source: Path) -> dict:
    """Synthetic compact file paired with the official-source audit contract."""
    return {
        "source_url": MODULE.PINNED_SOURCE_URL,
        "source_size": MODULE.PINNED_SOURCE_SIZE,
        "source_etag": MODULE.PINNED_SOURCE_ETAG,
        "expected_member_count": MODULE.PINNED_MEMBER_COUNT,
        "member_count": MODULE.PINNED_MEMBER_COUNT,
        "integrity_pass": True,
        "schema_pass": True,
        "all_members_verified": True,
        "compact_sha256": MODULE.sha256_file(source),
        "totals": {
            "row_count": MODULE.PINNED_ROW_COUNT,
            "official_row_count_match": True,
            "time_aligned_5min": True,
            "official_days_only": True,
            "schema_consistent_across_members": True,
        },
    }


class MaasInstanceRiskTest(unittest.TestCase):
    def setUp(self):
        self.start = 140 * 86400
        self.t = self.start + 4 * 300

    def test_decision_and_label_windows_exclude_unavailable_gap(self):
        data = series("inst-a", self.start, 20)
        data.loc[data.time_s.eq(self.t + 300), "sm_util"] = 99
        example, _ = MODULE.construct_examples(data)
        row = candidate_at(example, self.t)
        self.assertEqual(int(row.decision_s), self.t + 600)
        self.assertEqual(int(row.y), 0)  # t+300 is intentionally outside the label.
        self.assertEqual(float(row.sm_current), 10)
        changed = data.copy()
        changed.loc[changed.time_s.eq(self.t + 600), "sm_util"] = 85
        after, _ = MODULE.construct_examples(changed)
        changed_row = candidate_at(after, self.t)
        self.assertEqual(int(changed_row.y), 1)
        for feature in MODULE.FEATURES:
            self.assertAlmostEqual(float(row[feature]), float(changed_row[feature]), msg=feature)

    def test_label_requires_all_six_unique_valid_future_windows(self):
        data = series("inst-a", self.start, 20)
        data.loc[data.time_s.eq(self.t + 600), "sm_util"] = 85
        intact, _ = MODULE.construct_examples(data)
        self.assertEqual(int(candidate_at(intact, self.t).y), 1)

        missing = data.loc[~data.time_s.eq(self.t + 900)].copy()
        result, diagnostics = MODULE.construct_examples(missing)
        self.assertFalse(result.time_s.eq(self.t).any())
        self.assertGreater(diagnostics["splits"]["train"]["reason_counts_nonexclusive"]["missing_future"], 0)

        duplicated = pd.concat([data, data.loc[data.time_s.eq(self.t + 1200)]], ignore_index=True)
        result, diagnostics = MODULE.construct_examples(duplicated)
        self.assertFalse(result.time_s.eq(self.t).any())
        self.assertGreater(diagnostics["splits"]["train"]["reason_counts_nonexclusive"]["duplicate_future"], 0)

        invalid = data.copy()
        invalid.loc[invalid.time_s.eq(self.t + 1500), "samples"] = 0
        result, diagnostics = MODULE.construct_examples(invalid)
        self.assertFalse(result.time_s.eq(self.t).any())
        self.assertGreater(diagnostics["splits"]["train"]["reason_counts_nonexclusive"]["invalid_future"], 0)

    def test_missing_unused_gap_does_not_invalidate_label(self):
        data = series("inst-a", self.start, 20)
        data = data.loc[~data.time_s.eq(self.t + 300)]
        example, _ = MODULE.construct_examples(data)
        self.assertEqual(int(candidate_at(example, self.t).y), 0)

    def test_null_time_and_cluster_are_preserved_in_audit_without_becoming_features(self):
        data = series("inst-a", self.start, 20)
        data["time_s"] = data.time_s.astype("Int64")
        data["cluster_id"] = pd.Series([pd.NA] * len(data), dtype="string")
        data.loc[0, "time_s"] = pd.NA
        data.loc[0, "alarm_tie_hash"] = pd.NA
        result, audit = MODULE.construct_examples(data)
        self.assertEqual(audit["rows_without_time"], 1)
        self.assertEqual(int(candidate_at(result, self.t).y), 0)
        self.assertNotIn("cluster_id", MODULE.FEATURES)
        self.assertNotIn("instance_hash", MODULE.FEATURES)
        self.assertNotIn("future_gpu_count_changed", MODULE.FEATURES)

    def test_features_use_own_instance_and_only_three_past_windows(self):
        one = series("inst-a", self.start, 20)
        two = series("inst-b", self.start, 20, sm=85)
        data = pd.concat([two, one], ignore_index=True)  # Deliberately unordered.
        example, _ = MODULE.construct_examples(data)
        a_hash = hashlib.sha256(b"inst-a").hexdigest()
        a = example.loc[example.instance_hash.eq(a_hash) & example.time_s.eq(self.t)].iloc[0]
        self.assertEqual(float(a.sm_oldest), 10)
        self.assertEqual(float(a.sm_current), 10)
        self.assertEqual(int(a.current_threshold), 0)
        self.assertEqual(int(a.any_recent_threshold), 0)
        self.assertEqual(int(a.y), 0)
        b = example.loc[~example.instance_hash.eq(a_hash) & example.time_s.eq(self.t)].iloc[0]
        self.assertEqual(int(b.current_threshold), 1)
        self.assertEqual(int(b.y), 1)

    def test_split_uses_decision_time_and_keeps_labels_inside_boundary(self):
        data = series("inst-a", self.start, 7 * 288)
        example, diagnostics = MODULE.construct_examples(data)
        for split, (start, end) in MODULE.SPLITS.items():
            group = example.loc[example.split.eq(split)]
            self.assertGreater(len(group), 0)
            self.assertGreaterEqual(int(group.decision_s.min()), start)
            self.assertLessEqual(int((group.decision_s + 1800 + 300).max()), end)
            self.assertEqual(int(group.decision_s.max()), end - 2100)
            self.assertFalse(group.decision_s.eq(end - 1800).any())
            self.assertGreater(diagnostics["splits"][split]["candidates"], 0)
        validation = example.loc[example.split.eq("validation")]
        test = example.loc[example.split.eq("test")]
        self.assertEqual(int(validation.decision_s.min()), 144 * 86400)
        self.assertEqual(int(test.decision_s.min()), 145 * 86400)

    def test_top5_percent_uses_ingested_original_id_tiebreak(self):
        frame = pd.DataFrame({
            "day": [145] * 20, "y": [1] + [0] * 19,
            "instance_hash": [f"{i:064x}" for i in range(20)],
            "alarm_tie_hash": ["0" * 64] + [f"{i+1:064x}" for i in range(19)],
        })
        metrics = MODULE._alarm_metrics(frame, np.full(20, 0.2))
        self.assertEqual(metrics["alarms"], 1)
        self.assertEqual(metrics["true_positive_alarms"], 1)
        self.assertEqual(metrics["recall"], 1.0)

    def test_scoring_fits_one_selected_logistic_and_reports_all_baselines(self):
        data = series("inst-a", self.start, 7 * 288)
        data.loc[(data.time_s // 300).mod(12).eq(0), "sm_util"] = 85
        examples, _ = MODULE.construct_examples(data)
        result, test, model = MODULE.run_scoring(examples)
        self.assertIn(result["selection"]["selected_C"], MODULE.CS)
        self.assertIn(result["selection"]["selected_baseline"], MODULE.BASELINES)
        self.assertEqual(set(result["test"]["overall"]["methods"]), set(MODULE.METHODS))
        self.assertEqual(set(result["test"]["by_day"]), {"145", "146"})
        self.assertEqual(len(test), result["test"]["overall"]["n"])
        self.assertTrue(np.isfinite(test.pred_logistic.to_numpy()).all())
        self.assertGreaterEqual(float(test.pred_logistic.min()), 0)
        self.assertLessEqual(float(test.pred_logistic.max()), 1)
        if hasattr(model, "feature_names_in_"):
            self.assertEqual(list(model.feature_names_in_), list(MODULE.FEATURES))

    def test_source_gate_precedes_fit_and_sparse_sample_stops_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audit, output = root / "sample.parquet", root / "audit.json", root / "results"
            series("inst-a", self.start, 20).to_parquet(source)
            audit.write_text(json.dumps(valid_audit(source)))
            output.mkdir()
            (output / "logistic_scaler.joblib").write_bytes(b"old model")
            (output / "test_predictions.parquet").write_bytes(b"old predictions")
            with patch.object(MODULE, "fit_and_select", side_effect=AssertionError("must not fit")):
                result = MODULE.evaluate(source, audit, output)
            self.assertTrue(result["source_gate"]["passes"])
            self.assertFalse(result["sample_gates"]["passes"])
            self.assertFalse(result["scoring_performed"])
            self.assertFalse((output / "test_predictions.parquet").exists())
            self.assertFalse((output / "logistic_scaler.joblib").exists())

            bad = json.loads(audit.read_text())
            bad["all_members_verified"] = False
            audit.write_text(json.dumps(bad))
            (output / "logistic_scaler.joblib").write_bytes(b"old model")
            (output / "test_predictions.parquet").write_bytes(b"old predictions")
            with patch.object(MODULE, "load_compact", side_effect=AssertionError("must not load")):
                result = MODULE.evaluate(source, audit, output)
            self.assertFalse(result["source_gate"]["passes"])
            self.assertFalse(result["scoring_performed"])
            self.assertFalse((output / "test_predictions.parquet").exists())
            self.assertFalse((output / "logistic_scaler.joblib").exists())

    def test_source_gate_checks_pinned_identity_and_audit_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audit = root / "sample.parquet", root / "audit.json"
            series("inst-a", self.start, 20).to_parquet(source)
            fixture = valid_audit(source)
            audit.write_text(json.dumps(fixture))
            self.assertEqual(MODULE.verify_source(source, audit)["compact_sha256"], fixture["compact_sha256"])
            changes = (
                ("source_url", "https://wrong.example/trace.zip"),
                ("source_size", 1),
                ("source_etag", "wrong"),
                ("member_count", 20),
                ("expected_member_count", 20),
                ("integrity_pass", False),
                ("schema_pass", False),
                ("all_members_verified", False),
                ("compact_sha256", "0" * 64),
            )
            for field, value in changes:
                with self.subTest(field=field):
                    changed = {**fixture, field: value}
                    audit.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        MODULE.verify_source(source, audit)
            total_changes = (
                ("row_count", MODULE.PINNED_ROW_COUNT - 1),
                ("official_row_count_match", False),
                ("time_aligned_5min", False),
                ("official_days_only", False),
                ("schema_consistent_across_members", False),
            )
            for field, value in total_changes:
                with self.subTest(total=field):
                    changed = {**fixture, "totals": {**fixture["totals"], field: value}}
                    audit.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        MODULE.verify_source(source, audit)

    def test_synthetic_end_to_end_records_model_and_prediction_hashes(self):
        data = pd.concat([series(f"inst-{i:02d}", self.start, 7 * 288) for i in range(30)], ignore_index=True)
        data.loc[(data.time_s // 300).mod(12).eq(0), "sm_util"] = 85
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audit, output = root / "sample.parquet", root / "audit.json", root / "results"
            data.to_parquet(source, index=False)
            audit.write_text(json.dumps(valid_audit(source)))
            result = MODULE.evaluate(source, audit, output)
            self.assertTrue(result["source_gate"]["passes"])
            self.assertTrue(result["sample_gates"]["passes"])
            self.assertTrue(result["scoring_performed"])
            self.assertEqual(result["timing"]["label_available_offset_from_decision_s"], 2100)
            self.assertEqual(result["fitted_model"]["sha256"], MODULE.sha256_file(output / "logistic_scaler.joblib"))
            self.assertEqual(result["test_predictions_sha256"], MODULE.sha256_file(output / "test_predictions.parquet"))
            self.assertEqual(json.loads((output / "metrics.json").read_text())["evaluation_script_sha256"], MODULE.sha256_file(SCRIPT))


if __name__ == "__main__":
    unittest.main()
