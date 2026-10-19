"""Invariants for joint latency/throughput sparse-transfer posterior."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cross_metric_anchor_transfer import (  # noqa: E402
    assert_independent_posterior_parity,
    joint_posterior_residual,
    joint_residual_prior,
    predict_with_joint_prior,
)


def fixture(*, target_shift: int = 0, donor_task: str = "text_classification"):
    coordinates = [(1, 32, 1), (2, 64, 2), (4, 128, 4)]
    targets = pd.DataFrame([
        {"workload_group": f"g{i}", "workload_task": "text_classification",
         "batch_size": batch, "max_length": length + target_shift,
         "request_concurrency": concurrency, "repeats": 3,
         "replica_count": 1, "cpu_core_limit": 2}
        for i, (batch, length, concurrency) in enumerate(coordinates)
    ])
    donors = []
    residuals = [
        [(0.4, -0.3), (0.1, -0.2), (-0.2, 0.1)],
        [(-0.1, 0.2), (0.3, -0.4), (0.2, -0.1)],
        [(0.3, -0.2), (-0.2, 0.1), (0.1, -0.3)],
    ]
    for model_index, values in enumerate(residuals):
        for (batch, length, concurrency), (latency, rps) in zip(coordinates, values):
            donors.append({
                "model_id": f"donor{model_index}", "workload_task": donor_task,
                "batch_size": batch, "max_length": length,
                "request_concurrency": concurrency, "repeats": 3,
                "residual_latency": latency, "residual_rps": rps,
            })
    return targets, pd.DataFrame(donors)


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def full_slate():
    rows = []
    for i in range(9):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    "campaign": "main", "model_id": "held", "workload_group": f"g{i}",
                    "execution_id": f"e{i}_{gpu}_{cores}",
                    "workload_task": "text_classification",
                    "batch_size": 2 ** (i % 3), "max_length": 32 * (i + 1),
                    "request_concurrency": 1 + i, "repeats": 3,
                    "cpu_threads": cores, "replica_count": gpu,
                    "cpu_core_limit": cores, "latency_slo_ms": 100.0,
                    "target_throughput_rps": 2.0,
                    "latency_p95_ms": 25.0 + i + gpu,
                    "examples_per_second": 10.0 + gpu,
                    "result_json": json.dumps({"model_parameter_bytes": 123456}),
                })
    return pd.DataFrame(rows)


def full_donors(slate):
    anchors = slate[(slate.replica_count == 1) & (slate.cpu_core_limit == 2)]
    rows = []
    for model, multiplier in (("a", 1.0), ("b", -0.8), ("c", 0.5)):
        for i, item in enumerate(anchors.itertuples()):
            rows.append({
                "model_id": model, "workload_task": item.workload_task,
                "batch_size": item.batch_size, "max_length": item.max_length,
                "request_concurrency": item.request_concurrency,
                "repeats": item.repeats,
                "residual_latency": (0.1 + 0.04 * i) * multiplier,
                "residual_rps": (-0.2 - 0.02 * i) * multiplier,
            })
    return pd.DataFrame(rows)


class CrossMetricTransferTests(unittest.TestCase):
    def test_aligned_prior_is_psd_and_independent_posterior_matches_legacy(self) -> None:
        target, donors = fixture()
        joint, independent, info = joint_residual_prior(target, donors)
        self.assertEqual(info["mode"], "aligned_joint_prior")
        self.assertGreaterEqual(float(np.linalg.eigvalsh(joint)[0]), -1e-10)
        self.assertTrue(np.allclose(independent[0::2, 1::2], 0))
        from cross_model_covariance import residual_covariance
        legacy, _ = residual_covariance(target, donors)
        observed = np.array([0, 2])
        values = np.array([[0.25, -0.15], [-0.3, 0.2]])
        assert_independent_posterior_parity(independent, legacy, observed, values)
        joint_prediction = joint_posterior_residual(joint, observed, values)
        self.assertTrue(np.array_equal(joint_prediction[observed], values))

    def test_interpolated_prior_keeps_old_diagonal_and_no_donor_falls_back(self) -> None:
        target, donors = fixture(target_shift=1)
        joint, independent, info = joint_residual_prior(target, donors,
                                                         interpolate=True)
        self.assertEqual(info["mode"], "interpolated_joint_prior")
        from cross_model_covariance_external import continuous_prior
        legacy, _ = continuous_prior(target, donors)
        assert_independent_posterior_parity(
            independent, legacy, np.array([1]), np.array([[0.2, -0.1]]))
        self.assertGreaterEqual(float(np.linalg.eigvalsh(joint)[0]), -1e-10)
        _, unrelated = fixture(donor_task="causal_lm_forward")
        joint, independent, info = joint_residual_prior(target, unrelated)
        self.assertEqual(info["mode"], "independent_kernel_fallback")
        self.assertTrue(np.array_equal(joint, independent))

    def test_cross_metric_link_changes_only_unobserved_predictions(self) -> None:
        target, donors = fixture()
        joint, independent, _ = joint_residual_prior(target, donors)
        observed = np.array([0])
        values = np.array([[0.35, 0.0]])
        coupled = joint_posterior_residual(joint, observed, values)
        separate = joint_posterior_residual(independent, observed, values)
        self.assertTrue(np.array_equal(coupled[observed], values))
        self.assertTrue(np.array_equal(separate[observed], values))
        self.assertGreater(float(np.max(np.abs(coupled[1:, 1] - separate[1:, 1]))),
                           1e-4)

    def test_invalid_prior_and_observations_are_rejected(self) -> None:
        target, donors = fixture()
        with self.assertRaisesRegex(ValueError, "invalid_smooth_weight"):
            joint_residual_prior(target, donors, rho=2.0)
        prior, _, _ = joint_residual_prior(target, donors)
        with self.assertRaisesRegex(ValueError, "invalid_joint_observations"):
            joint_posterior_residual(prior, np.array([0, 0]),
                                     np.array([[1., 0.], [1., 0.]]))

    def test_unprobed_outcomes_cannot_change_joint_prediction(self) -> None:
        slate = full_slate()
        donors = full_donors(slate)
        joint, independent, _ = joint_residual_prior(slate, donors)
        from cross_model_covariance import residual_covariance
        legacy, _ = residual_covariance(slate, donors)
        probes = [f"g{i}" for i in range(8)]
        predictor = ZeroPredictor()
        original = predict_with_joint_prior(
            slate, probes, joint, independent, legacy, predictor, predictor)
        changed = slate.copy()
        observed = (changed.workload_group.isin(probes)
                    & changed.replica_count.eq(1) & changed.cpu_core_limit.eq(2))
        changed.loc[~observed, "latency_p95_ms"] *= 100
        changed.loc[~observed, "examples_per_second"] /= 100
        repeated = predict_with_joint_prior(
            changed, probes, joint, independent, legacy, predictor, predictor)
        pd.testing.assert_frame_equal(original, repeated)


if __name__ == "__main__":
    unittest.main()
