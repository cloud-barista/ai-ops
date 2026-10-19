"""Synthetic checks for SwissAI time ordering, feedback, and frozen policies."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_swissai_request_duration", SCRIPTS / "evaluate_swissai_request_duration.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
H = MODULE.HOUR_US
D = MODULE.DAY_US


def small_trace(created: list[int], finished: list[int], *,
                model: list[int] | None = None, created_naive: list[bool] | None = None,
                finished_naive: list[bool] | None = None) -> MODULE.Trace:
    n = len(created)
    model = model if model is not None else [0] * n
    created_naive = created_naive if created_naive is not None else [False] * n
    finished_naive = finished_naive if finished_naive is not None else [False] * n
    return MODULE.Trace(
        created_us=np.array(created, np.int64),
        finished_us=np.array(finished, np.int64),
        finished_present=np.ones(n, bool),
        model_code=np.array(model, np.int32), model_names=["A", "B"],
        status_code=np.zeros(n, np.int32), status_names=["DEFAULT"],
        created_naive=np.array(created_naive, bool),
        finished_naive=np.array(finished_naive, bool),
        source_row=np.arange(n, dtype=np.int32),
        audit={"counts": {"source_rows": n}},
        compact_sha256="unused", audit_sha256="unused",
    )


class SwissAiDurationTest(unittest.TestCase):
    def test_loads_compact_parquet_with_null_finish_and_checks_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            table = pa.table({
                "created_utc_us": pa.array([1, 2, 2], pa.int64()),
                "finished_utc_us": pa.array([3, None, 4], pa.int64()),
                "elapsed_us": pa.array([2, None, 2], pa.int64()),
                "model": pa.array(["A", "<missing>", "B"]),
                "status": pa.array(["DEFAULT", "ERROR", "DEFAULT"]),
                "created_naive": pa.array([False, False, True]),
                "finished_naive": pa.array([False, False, True]),
                "source_row": pa.array([0, 1, 2], pa.int32()),
            })
            pq.write_table(table, root / "requests_sorted.parquet")
            (root / "audit.json").write_text(json.dumps({
                "source": {"sha256": MODULE.PINNED_SOURCE_SHA256},
                "compact": {"rows": 3},
                "counts": {"source_rows": 3},
            }))
            reviewed_hash = MODULE.PINNED_COMPACT_SHA256
            MODULE.PINNED_COMPACT_SHA256 = MODULE.sha256(root / "requests_sorted.parquet")
            try:
                loaded = MODULE.load_trace(root)
            finally:
                MODULE.PINNED_COMPACT_SHA256 = reviewed_hash
            self.assertEqual(loaded.finished_present.tolist(), [True, False, True])
            self.assertIn("<MISSING>", loaded.model_names)

    def test_arrival_features_exclude_same_timestamp_and_one_hour_old(self) -> None:
        t = np.array([0, 0, H, H, H + 1, 2 * H], np.int64)
        codes = np.array([0, 1, 0, 0, 1, 0], np.int32)
        global_count, model_count = MODULE.arrival_counts(t, codes)
        self.assertEqual(global_count.tolist(), [0, 0, 2, 2, 2, 3])
        self.assertEqual(model_count.tolist(), [0, 0, 1, 1, 0, 2])

    def test_training_label_must_be_available_by_day30(self) -> None:
        t = [29 * D, 29 * D + H, 30 * D, 40 * D]
        f = [29 * D + H, 30 * D, 30 * D + H, 40 * D + H]
        p = MODULE.prepare(small_trace(t, f))
        p.day0_us = 0
        p.day = (p.created_us // D).astype(np.int32)
        # The second completion is exactly at day30: an extra hour makes it late.
        self.assertEqual(MODULE.training_indices(p).tolist(), [0])
        self.assertEqual(MODULE.training_indices(p, delay_hours=0).tolist(), [0, 1])

    def test_reanchored_training_excludes_requests_before_exploratory_day_zero(self) -> None:
        t = [0, 10 * D, 11 * D]
        f = [H, 10 * D + H, 11 * D + H]
        trace = small_trace(t, f)
        p = MODULE.reanchor(MODULE.prepare(trace), 10 * D)
        self.assertEqual(MODULE.eligible_training_indices(p).tolist(), [1, 2])
        gate = MODULE.quality_gate(p, trace)
        self.assertEqual(gate["training_eligible_labels_at_day30"], 2)

    def test_feedback_depends_on_completion_not_request_order(self) -> None:
        t = np.array([0, 1, 2, 4, 7], np.int64) * H
        f = np.array([12, 2, 4, 5, 8], np.int64) * H
        y = (f - t) / MODULE.US
        base = np.full(len(t), 10.0)
        models = np.zeros(len(t), np.int32)
        global_pred, model_pred, support = MODULE.causal_rolling_predictions(
            t, f, y, base, models, delay_hours=1
        )
        self.assertEqual(support.tolist(), [0, 0, 0, 1, 3])
        self.assertAlmostEqual(global_pred[3], 10.0 + (3600 - 10))
        self.assertAlmostEqual(model_pred[3], global_pred[3])
        changed = y.copy()
        changed[0] = 100_000.0  # row 0 still cannot be known by hour 7
        other, _, _ = MODULE.causal_rolling_predictions(t, f, changed, base, models,
                                                         delay_hours=1)
        np.testing.assert_array_equal(global_pred, other)

    def test_guard_uses_available_validation_only_and_stays_frozen(self) -> None:
        start = 40
        t = [30 * D, 31 * D, 39 * D, 40 * D, 41 * D]
        f = [31 * D, 32 * D, 40 * D, 40 * D + H, 41 * D + H]
        p = MODULE.prepare(small_trace(t, f))
        p.day0_us = 0
        p.day = (p.created_us // D).astype(np.int32)
        predictions = np.zeros(5, float)
        old_blocks = p.complete_blocks
        p.complete_blocks = [start]
        old_min = MODULE.GUARD_VALIDATION_MINIMUM
        MODULE.GUARD_VALIDATION_MINIMUM = 2
        try:
            guard, records, unavailable = MODULE.block_policies(
                p, np.arange(5), predictions, delay_hours=1, n_models=2,
            )
        finally:
            MODULE.GUARD_VALIDATION_MINIMUM = old_min
            p.complete_blocks = old_blocks
        self.assertEqual(unavailable, [])
        self.assertEqual(records[0]["calibration_available_labels"], 2)
        self.assertTrue(np.isnan(guard[:3]).all())
        self.assertEqual(guard[3], guard[4])

    def test_timezone_reinterpretation_resorts_and_exposes_negative_duration(self) -> None:
        # The second row's naive arrival moves one hour earlier under UTC+1.
        t = [2 * H, 2 * H + H // 2]
        f = [2 * H + H // 2, 2 * H + H // 2 + H // 4]
        trace = small_trace(t, f, created_naive=[False, True],
                            finished_naive=[False, False])
        p = MODULE.prepare(trace, timezone_offset_hours=1)
        self.assertEqual(p.source_row.tolist(), [1, 0])
        self.assertEqual(p.positive.tolist(), [True, True])
        # A naive finish against an explicit creation can turn negative.
        trace2 = small_trace([H], [H + H // 2], finished_naive=[True])
        self.assertFalse(MODULE.prepare(trace2, 1).positive[0])

    def test_guard_minimum_order_statistic_and_group_aggregation(self) -> None:
        labels = np.array([1.0, 2.0, 3.0, 100.0])
        pred = np.zeros(4)
        self.assertEqual(MODULE.minimum_margin(labels, pred, target=0.5), 2.0)
        grouped = MODULE._group_scores(
            labels, {"global": pred, "guard92": np.array([2.0, 2.0, np.nan, np.nan])},
            np.array([0, 0, 1, 1]), ["first", "second"],
        )
        self.assertEqual(grouped["first"]["methods"]["guard92"]["n"], 2)
        self.assertIsNone(grouped["second"]["methods"]["guard92"])

    def test_quality_gate_enforces_full_test_block_and_model_availability(self) -> None:
        t = np.array([0, 40 * D, 41 * D, 50 * D], np.int64)
        f = t + H
        trace = small_trace(t.tolist(), f.tolist())
        trace.finished_present[2] = False
        p = MODULE.prepare(trace)
        gate = MODULE.quality_gate(p, trace)
        self.assertFalse(gate["passes"])
        self.assertEqual(gate["complete_test_blocks"]["40"]["positive_fraction"], 0.5)
        self.assertIn("40", gate["failing_blocks"])
        self.assertEqual(gate["training_eligible_labels_at_day30"], 1)
        self.assertEqual(gate["first_calibration_available_labels_at_day40"], 0)

    def test_empty_complete_block_is_reported_separately(self) -> None:
        t = [0, 40 * D, 50 * D, 110 * D]
        f = [value + H for value in t]
        gate = MODULE.quality_gate(MODULE.prepare(small_trace(t, f)), small_trace(t, f))
        self.assertIn("60", gate["empty_blocks"])
        self.assertIsNone(gate["complete_test_blocks"]["60"]["positive_fraction"])
        self.assertIn("complete test blocks contain no requests", gate["reasons"])

    def test_day_zero_uses_first_parsed_creation_and_requires_next_midnight(self) -> None:
        t = [0, D, 41 * D, 109 * D]
        f = [H, D + H, 41 * D + H, 109 * D + H]
        trace = small_trace(t, f)
        trace.finished_present[0] = False
        p = MODULE.prepare(trace)
        self.assertEqual(p.day0_us, 0)
        # A request on day 109 does not establish that day 109 ended.
        self.assertEqual(p.complete_blocks[-1], 90)
        self.assertEqual(p.full_test_end_day, 100)
        completed = MODULE.prepare(small_trace(t + [110 * D], f + [110 * D + H]))
        self.assertEqual(completed.complete_blocks[-1], 100)
        self.assertEqual(completed.full_test_end_day, 110)

    def test_missing_model_never_takes_a_top_category(self) -> None:
        codes = np.array([0, 0, 0, 1, 1, 2, 3, 3, 3, 3], np.int32)
        names = ["<MISSING>", "A", "B", "\\N"]
        mapping, top = MODULE.category_mapping(codes, np.arange(10), names)
        self.assertEqual(top, ["A", "B"])
        self.assertEqual(mapping[0], MODULE.MAX_MODEL_CATEGORIES)
        self.assertEqual(mapping[3], MODULE.MAX_MODEL_CATEGORIES)

    def test_key_like_model_is_redacted_in_report_names(self) -> None:
        synthetic_key = "sk-" + "syntheticexample1234567890"
        alias = MODULE.safe_model_name(synthetic_key)
        self.assertTrue(alias.startswith("<REDACTED_MODEL_SHA256_"))
        self.assertNotIn(synthetic_key, alias)
        trace = small_trace([0, 50 * D], [H, 50 * D + H])
        trace.model_names[0] = synthetic_key
        report = MODULE.quality_gate(MODULE.prepare(trace), trace)
        self.assertNotIn(synthetic_key, json.dumps(report))

    def test_numeric_test_prediction_export_omits_raw_identifiers(self) -> None:
        t = [0, 40 * D, 41 * D, 50 * D]
        p = MODULE.prepare(small_trace(t, [value + H for value in t]))
        pred = np.array([1.0, 2.0, 3.0, 4.0])
        guarded = np.array([np.nan, np.nan, 4.0, np.nan])
        with tempfile.TemporaryDirectory() as temporary:
            meta = MODULE.save_test_predictions(
                Path(temporary), p, np.arange(4), pred, pred, pred, guarded,
            )
            table = pq.read_table(meta["path"])
            self.assertEqual(meta["rows"], 2)
            self.assertEqual(table.num_rows, 2)
            self.assertEqual(table["guard92_p90_seconds"].null_count, 1)
            self.assertEqual(table.schema.names, meta["columns"])
            self.assertTrue(all(pa.types.is_integer(field.type) or pa.types.is_floating(field.type)
                                for field in table.schema))

    def test_summary_keeps_guard_comparison_on_same_requests(self) -> None:
        t = [0, 40 * D, 41 * D, 50 * D]
        trace = small_trace(t, [value + H for value in t])
        p = MODULE.prepare(trace)
        pred = np.full(4, 3600.0)
        guard = np.array([np.nan, np.nan, 3600.0, np.nan])
        summary = MODULE.summarize_scores(
            p, trace, np.arange(4), pred, pred, pred, guard,
            np.zeros(4, np.int16),
        )
        self.assertEqual(summary["all"]["global"]["n"], 2)
        self.assertEqual(summary["all"]["guard92"]["n"], 1)
        self.assertEqual(summary["paired_guard_available"]["methods"]["global"]["n"], 1)

    def test_exploratory_start_uses_only_arrival_counts_and_first_valid_date(self) -> None:
        created = np.array([0] + [30 * D] * 3 + [40 * D] * 2 + [111 * D], np.int64)
        selected = MODULE.select_exploratory_start(
            created, min_fit_requests=3, min_initialization_requests=2,
            original_day0_utc_us=0,
        )
        self.assertEqual(selected["selected_offset_days"], 1)
        self.assertEqual(selected["fit_window_all_created_requests"], 3)
        self.assertEqual(selected["initialization_window_all_created_requests"], 2)


if __name__ == "__main__":
    unittest.main()
