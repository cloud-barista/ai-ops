import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_swissai_qwen_prefix as evaluator
from swissai_prefix_cache_core import CacheRequest, default_policy_specs, replay_requests


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def score_summary(positive: int, negative: int, days=None):
    return {"counts": {"positive": positive, "negative": negative}, "days": days or {}}


class QwenPrefixEvaluatorTests(unittest.TestCase):
    def test_daily_hash_sample_is_label_independent_and_deterministic(self):
        day = evaluator.DAY_US
        rows = [
            (evaluator.HORIZON_US, (9).to_bytes(32, "big"), 16, (1,)),
            (evaluator.HORIZON_US + 1, (1).to_bytes(32, "big"), 16, (2,)),
            (evaluator.HORIZON_US + 2, (3).to_bytes(32, "big"), 16, (3,)),
            (day + evaluator.HORIZON_US, (7).to_bytes(32, "big"), 16, (4,)),
            (day + evaluator.HORIZON_US + 1, (2).to_bytes(32, "big"), 16, (5,)),
            (2 * day - evaluator.HORIZON_US, (0).to_bytes(32, "big"), 16, (6,)),
        ]
        expected = [1, 2, 3, 4]  # Last row is at the censored boundary.
        first = evaluator.sample_training_indices(rows, 0, 2 * day, per_day=2)
        second = evaluator.sample_training_indices(iter(rows), 0, 2 * day, per_day=2)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(evaluator.selection_digest(first), evaluator.selection_digest(second))

    def test_support_gate_uses_full_classes_and_daily_validation_support(self):
        days = {f"d{i}": {"positive": 101, "negative": 101} for i in range(3)}
        passed = evaluator.support_gate(score_summary(10_000, 10_000),
                                        score_summary(10_000, 10_000, days))
        self.assertTrue(passed["passed"])
        days["d2"]["negative"] = 99
        failed = evaluator.support_gate(score_summary(10_000, 10_000),
                                        score_summary(10_000, 10_000, days))
        self.assertFalse(failed["passed"])
        self.assertIn("validation_supported_days_below_3", failed["reasons"])

    def test_validation_policy_selects_lower_tau_on_tie_and_checks_both_baselines(self):
        def entry(ratio):
            return {"total": {"hit_ratio": ratio}, "days": {}}

        cache = {"lru_c4096": entry(0.20), "lfu_c4096": entry(0.22)}
        for tau, value in zip(evaluator.THRESHOLDS, (0.20, 0.23, 0.24, 0.24, 0.18, 0.17)):
            cache[evaluator._admission_name(tau, 4096)] = entry(value)
        cache[evaluator._admission_name(0.0, 4096)] = entry(0.20)
        selected = evaluator.choose_validation_policy(cache)
        self.assertEqual(selected["selected_tau"], 0.2)
        self.assertTrue(selected["gate_passed"])
        cache["lfu_c4096"] = entry(0.23)
        self.assertFalse(evaluator.choose_validation_policy(cache)["gate_passed"])

    def test_tau_zero_matches_full_lru_replay(self):
        start = evaluator.HORIZON_US
        rows = [
            CacheRequest(start, (1, 2), digest("a"), 0.0),
            CacheRequest(start + 1, (1, 3), digest("b"), 0.2),
            CacheRequest(start + 2, (4, 5), digest("c"), 0.7),
            CacheRequest(start + 3, (1, 2), digest("d"), 1.0),
        ]
        cache = replay_requests(rows, default_policy_specs((2,), (0.0, 0.1)),
                                split_start_us=0, split_end_us=3 * start)
        evaluator.check_tau_zero(cache, 2)
        self.assertEqual(cache["lru_c2"]["total"], cache["admit_t0p0_c2"]["total"])

    def test_validation_stream_does_not_yield_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "compact.parquet"
            train = evaluator.bounds("train")[0] + 1
            validation = evaluator.bounds("validation")[0] + 1
            heldout = evaluator.bounds("locked_test")[0] + 1
            table = pa.table({
                "created_utc_us": pa.array([train, validation, heldout], type=pa.int64()),
                "id_sha256": pa.array([digest("a"), digest("b"), digest("c")], type=pa.binary()),
                "token_count": pa.array([16, 16, 16], type=pa.int64()),
                "bucket_ids": pa.array([[1], [2], [3]], type=pa.list_(pa.uint64())),
            })
            pq.write_table(table, source, row_group_size=1)
            with patch.object(evaluator, "SOURCE", source):
                rows = list(evaluator.stream_rows("validation"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], validation)

    def test_closed_gate_blocks_source_and_test_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(evaluator, "TEST", Path(directory) / "test.json"),
                  patch.object(evaluator, "preflight_test", side_effect=ValueError("closed")),
                  patch.object(evaluator, "verify_source") as source,
                  patch.object(evaluator, "stream_rows") as rows):
                with self.assertRaisesRegex(ValueError, "closed"):
                    evaluator.test()
                source.assert_not_called()
                rows.assert_not_called()

    def test_manifest_detects_model_tamper_before_compact_access(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            model = directory / "model.joblib"
            support = directory / "support.json"
            manifest_path = directory / "manifest.json"
            model.write_bytes(b"frozen model")
            support.write_text("{}")
            code = {"test": "pinned"}
            manifest = {
                "kind": "swissai_qwen_prefix_model_v1", "code": code,
                "features": list(evaluator.FEATURES), "parameters": evaluator.PARAMETERS,
                "splits": evaluator.SPLIT_RECORD, "sklearn_version": evaluator.sklearn.__version__,
                "model_sha256": evaluator.sha256(model), "support_sha256": evaluator.sha256(support),
                "model_path": str(model),
            }
            manifest_path.write_text(json.dumps(manifest))
            with (patch.object(evaluator, "MODEL", model),
                  patch.object(evaluator, "SUPPORT", support),
                  patch.object(evaluator, "MANIFEST", manifest_path),
                  patch.object(evaluator, "verify_fixed_code", return_value=code),
                  patch.object(evaluator, "verify_source") as source):
                evaluator.load_manifest(verify_compact=False)
                model.write_bytes(b"tampered model")
                with self.assertRaisesRegex(ValueError, "manifest_or_code_mismatch"):
                    evaluator.load_manifest(verify_compact=False)
                source.assert_not_called()

    def test_synthetic_prepare_validate_stays_out_of_test_split(self):
        """Exercise serialization and Parquet streaming without real labels."""
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            compact = directory / "compact.parquet"
            quality = directory / "quality.json"
            artifacts = directory / "artifacts"
            starts = [evaluator.bounds(name)[0] for name in ("train", "validation")]
            rows = []
            for split_index, start in enumerate(starts):
                for group in range(120):
                    for repeat in range(2):
                        minute = 61 + 2 * group + repeat
                        rows.append((start + minute * 60_000_000,
                                     digest(f"{split_index}-{group}-{repeat}"),
                                     16, [group + 1]))
            table = pa.table({
                "created_utc_us": pa.array([r[0] for r in rows], type=pa.int64()),
                "id_sha256": pa.array([r[1] for r in rows], type=pa.binary()),
                "token_count": pa.array([r[2] for r in rows], type=pa.int64()),
                "bucket_ids": pa.array([r[3] for r in rows], type=pa.list_(pa.uint64())),
            })
            pq.write_table(table, compact, row_group_size=37)
            compact_sha = evaluator.sha256(compact)
            quality.write_text(json.dumps({
                "source": {"decompressed_sha256": "synthetic", "decompressed_bytes": 1,
                           "raw_rows": len(rows), "pinned_source_verified": True},
                "compact": {"sha256": compact_sha, "rows": len(rows)},
                "interpretation": {"future_reuse_labels_or_cache_outcomes_computed": False},
                "support_by_fixed_split": {
                    "train": {"eligible_rows": 240, "eligible_scoring_window": 240},
                    "validation": {"eligible_rows": 240, "eligible_scoring_window": 240},
                },
            }))
            original_gate = evaluator.support_gate

            def tiny_gate(train, validation):
                return original_gate(train, validation, minimum_each=1,
                                     minimum_days=1, minimum_daily_each=1)

            replacements = {
                "SOURCE": compact, "QUALITY": quality, "ARTIFACTS": artifacts,
                "MODEL": artifacts / "model.joblib", "MANIFEST": artifacts / "manifest.json",
                "SUPPORT": artifacts / "support.json", "VALIDATION": artifacts / "validation.json",
                "VALIDATION_LOCK": artifacts / "validation.lock.json",
                "TEST": artifacts / "test.json", "RAW_SHA": "synthetic", "RAW_ROWS": len(rows),
                "RAW_BYTES": 1, "QUALITY_SHA": evaluator.sha256(quality),
                "COMPACT_SHA": compact_sha, "support_gate": tiny_gate,
            }
            with ExitStack() as stack:
                for name, value in replacements.items():
                    stack.enter_context(patch.object(evaluator, name, value))
                prepared = evaluator.prepare()
                validated = evaluator.validate()
                self.assertTrue(prepared["support_gate"]["passed"])
                self.assertFalse(validated["selection"]["gate_passed"])
                self.assertFalse((artifacts / "test.json").exists())
                with patch.object(evaluator, "stream_rows", wraps=evaluator.stream_rows) as stream:
                    with self.assertRaisesRegex(ValueError, "test_remains_closed"):
                        evaluator.test()
                    self.assertNotIn("locked_test", [call.args[0] for call in stream.call_args_list])


if __name__ == "__main__":
    unittest.main()
