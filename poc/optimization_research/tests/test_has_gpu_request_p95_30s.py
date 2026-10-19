"""Synthetic clock, censoring, split, and gate checks for HAS-GPU evaluation."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_has_gpu_request_p95_30s.py"
SPEC = importlib.util.spec_from_file_location("evaluate_has_gpu_request_p95_30s", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
S = MODULE.SECOND_US


def trace_from_completion_duration(records: list[tuple[float, float]]) -> MODULE.RequestTrace:
    ordered = sorted(records)
    completion = np.array([round(t * S) for t, _ in ordered], dtype=np.int64)
    duration = np.array([d for _, d in ordered], dtype=np.float64)
    start = completion - np.rint(duration * S).astype(np.int64)
    order = np.argsort(start, kind="stable")
    return MODULE.RequestTrace("synthetic", "bert", completion, duration,
                               start[order], duration[order])


def sample_trace(*, future_duration: float = 1.0,
                 last_completion: float = 90.0,
                 past_count: int = 100,
                 future_count: int = 100,
                 extra: list[tuple[float, float]] | None = None) -> MODULE.RequestTrace:
    # First completion fixes the first anchor at t=30. The history is fully
    # complete by t; 100 future requests start after t. A tail record provides
    # the right-censoring margin without adding a request to the future window.
    past = [(1.0 + 0.2 * i, 1.0) for i in range(past_count)]
    future_starts = [31.0 + 0.2 * i for i in range(future_count)]
    future = [(start + future_duration, future_duration) for start in future_starts]
    return trace_from_completion_duration(
        [(0.0, 1.0)] + past + future + [(last_completion, 1.0)] + (extra or []))


class HasGpuRequestP95Test(unittest.TestCase):
    def test_anchor_uses_completed_history_and_future_starts_only(self) -> None:
        baseline = sample_trace()
        baseline_counts, baseline_anchors = MODULE.count_anchors(baseline)
        self.assertEqual(baseline_counts["candidate_anchors"], 1)
        self.assertEqual(baseline_counts["eligible_anchors"], 1)
        self.assertEqual((baseline_anchors[0].past_count,
                          baseline_anchors[0].future_count), (100, 100))
        baseline_example = MODULE.make_examples(baseline, baseline_anchors)[0]

        # It started before t and completed after t. It may affect neither
        # the as-of history nor the target of this anchor.
        inflight = sample_trace(extra=[(34.9, 5.0)])
        _, inflight_anchors = MODULE.count_anchors(inflight)
        inflight_example = MODULE.make_examples(inflight, inflight_anchors)[0]
        np.testing.assert_array_equal(inflight_example.features, baseline_example.features)
        self.assertEqual(inflight_example.y_log_ratio, baseline_example.y_log_ratio)

        # Changing only durations of requests starting after t changes the
        # label, while all four prediction-time features stay identical.
        longer_future = sample_trace(future_duration=5.0)
        _, longer_anchors = MODULE.count_anchors(longer_future)
        longer_example = MODULE.make_examples(longer_future, longer_anchors)[0]
        np.testing.assert_array_equal(longer_example.features, baseline_example.features)
        self.assertGreater(longer_example.y_log_ratio, baseline_example.y_log_ratio)

    def test_open_left_closed_right_boundaries(self) -> None:
        base_counts, base_anchors = MODULE.count_anchors(sample_trace())
        self.assertEqual(base_counts["eligible_anchors"], 1)
        self.assertEqual(base_anchors[0].past_count, 100)
        self.assertEqual(base_anchors[0].future_count, 100)

        # Completion exactly at t is known and included. Start exactly at t
        # is excluded, while start exactly at t+30 is included.
        augmented = sample_trace(extra=[(30.0, 1.0), (31.0, 1.0), (61.0, 1.0)])
        _, anchors = MODULE.count_anchors(augmented)
        self.assertEqual(anchors[0].past_count, 101)
        self.assertEqual(anchors[0].future_count, 101)

    def test_right_censoring_margin_is_required_before_percentiles(self) -> None:
        too_short = sample_trace(last_completion=89.999999)
        counts, anchors = MODULE.count_anchors(too_short)
        self.assertEqual(counts["candidate_anchors"], 0)
        self.assertEqual(anchors, [])
        enough = sample_trace(last_completion=90.0)
        counts, anchors = MODULE.count_anchors(enough)
        self.assertEqual(counts["eligible_anchors"], 1)
        self.assertEqual(anchors[0].t_us + 60 * S, enough.completion_us[-1])

    def test_count_threshold_is_exact_for_both_windows(self) -> None:
        for kwargs in ({"past_count": 99}, {"future_count": 99}):
            counts, anchors = MODULE.count_anchors(sample_trace(**kwargs))
            self.assertEqual(counts["candidate_anchors"], 1)
            self.assertEqual(counts["eligible_anchors"], 0)
            self.assertEqual(anchors, [])
        counts, anchors = MODULE.count_anchors(sample_trace())
        self.assertEqual(counts["eligible_anchors"], 1)
        self.assertEqual(anchors[0].past_count, 100)
        self.assertEqual(anchors[0].future_count, 100)

    def test_rejects_non_success_or_wrong_mode_before_windows(self) -> None:
        source = {"run": "1-bert", "model_family": "bert", "request_rows": 1}
        header = "duration,mode,status_code,success,timestamp\n"
        def parse(mode: str, status: str, success: str) -> MODULE.RequestTrace:
            row = f"1.0,{mode},{status},{success},2025-03-17T09:55:43.388511\n"
            return MODULE.parse_request_csv((header + row).encode(), source)
        self.assertEqual(len(parse("bert", "200", "True").completion_us), 1)
        for values in (("bert", "500", "False"), ("bert", "200", "False"),
                       ("image", "200", "True")):
            with self.assertRaises(ValueError):
                parse(*values)

    def test_count_gate_requires_runs_and_windows_in_each_family(self) -> None:
        rows = [{"run": f"{family}-{i}", "family": family,
                 "candidate_anchors": 5, "eligible_anchors": 5}
                for family in MODULE.FAMILIES for i in range(3)]
        self.assertTrue(MODULE.coverage_report(rows)["passed"])
        reduced = [dict(row) for row in rows]
        reduced[-1]["eligible_anchors"] = 0
        self.assertFalse(MODULE.coverage_report(reduced)["passed"])

    def test_ridge_scaling_and_training_mean_exclude_heldout_family(self) -> None:
        examples = [MODULE.Example(family, family, 0, np.ones(4),
                                   10.0 if family == "bert" else 0.0,
                                   1.0, math.exp(10.0) if family == "bert" else 1.0)
                    for family in MODULE.FAMILIES]
        report = MODULE.evaluate(examples)
        by_family = {row["family"]: row for row in report["per_run"]}
        self.assertAlmostEqual(
            by_family["bert"]["methods"]["training_mean_log_ratio"]["male"], 10.0)
        self.assertAlmostEqual(
            by_family["resnet"]["methods"]["training_mean_log_ratio"]["male"], 10 / 3)
        self.assertEqual(report["fold_training_windows"], {family: 3 for family in MODULE.FAMILIES})
        fit = MODULE.fit_ridge_train_only(np.array([[1., 4., 1., 8.], [2., 4., 1., 7.]]),
                                          np.array([0., 1.]))
        np.testing.assert_allclose(fit.x_mean, [1.5, 4., 1., 7.5])
        np.testing.assert_allclose(fit.x_scale, [0.5, 1., 1., 0.5])

    def test_linear_percentile_and_severe_threshold(self) -> None:
        past = np.arange(1., 101.)
        future = np.arange(2., 102.)
        trace = MODULE.RequestTrace("synthetic", "bert", np.arange(100), past,
                                    np.arange(100), future)
        anchor = MODULE.Anchor(30 * S, 0, 100, 0, 100, 30)
        example = MODULE.make_examples(trace, [anchor])[0]
        self.assertAlmostEqual(example.past_p95_s, 95.05)
        self.assertAlmostEqual(example.future_p95_s, 96.05)
        true = np.array([0.0, 0.0])
        pred = np.array([math.log(0.8), math.log(0.79)])
        metric = MODULE.prediction_metrics(true, pred)
        self.assertEqual(metric["severe_underprediction_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
