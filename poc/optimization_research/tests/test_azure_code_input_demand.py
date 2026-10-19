"""Synthetic timing and scoring checks for the fixed Azure Coding study."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_azure_code_input_demand as study


class AzureCodingDemandTests(unittest.TestCase):
    def test_aggregate_uses_only_time_and_context_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.csv"
            source.write_text(
                "TIMESTAMP,ContextTokens,GeneratedTokens\n"
                "2024-05-10 00:00:00+00:00,10,SECRET_A\n"
                "2024-05-10 00:04:59+00:00,20,SECRET_B\n"
                "2024-05-10 00:05:00+00:00,30,SECRET_C\n")
            tokens, counts, rows = study.aggregate_source(source)
            self.assertEqual(rows, 3)
            self.assertEqual(tokens[:2].tolist(), [30, 30])
            self.assertEqual(counts[:2].tolist(), [2, 1])
            self.assertEqual(int(counts[2:].sum()), 0)

    def test_current_or_future_target_cannot_change_current_features(self):
        y = np.full(study.N_SLOTS, 2_000., dtype=float)
        original = study.design_from_aggregates(y)
        y[1000] = 200_000.
        changed = study.design_from_aggregates(y)
        np.testing.assert_allclose(
            original.loc[:1000, list(study.FEATURES)].to_numpy(float),
            changed.loc[:1000, list(study.FEATURES)].to_numpy(float),
            equal_nan=True)
        self.assertEqual(original.loc[1000, "scale"], changed.loc[1000, "scale"])
        self.assertNotEqual(original.loc[1000, "ratio"], changed.loc[1000, "ratio"])
        self.assertNotEqual(original.loc[1001, "lag1_over_scale"],
                            changed.loc[1001, "lag1_over_scale"])

    def test_exact_burn_in_and_previous_day_residual_support(self):
        y = np.full(study.N_SLOTS, 2_000., dtype=float)
        frame = study.design_from_aggregates(y)
        self.assertTrue(frame.loc[:11, "scale"].isna().all())
        self.assertEqual(float(frame.loc[12, "scale"]), 2_000.)
        self.assertTrue(frame.loc[:299, "previous_day_ratio"].isna().all())
        self.assertEqual(float(frame.loc[300, "previous_day_ratio"]), 1.)
        self.assertEqual(int(frame.iloc[study.TRAIN]["previous_day_ratio"].notna().sum()),
                         564)
        self.assertEqual(len(frame.iloc[study.TRAIN]), 852)

    def test_scale_support_and_absolute_pinball_identity(self):
        y = np.full(study.N_SLOTS, 2_000., dtype=float)
        y[:12] = 999.
        frame = study.design_from_aggregates(y)
        self.assertLess(frame.loc[12, "scale"], study.MIN_SCALE)
        self.assertTrue(np.isnan(frame.loc[12, "ratio"]))
        target = np.array([3_000., 1_000.])
        scale = np.array([2_000., 4_000.])
        ratio_forecast = np.array([1.2, 1.2])
        absolute_forecast = scale * ratio_forecast
        np.testing.assert_allclose(study.pinball(target, absolute_forecast),
                                   scale * study.pinball(target / scale, ratio_forecast))

    def test_weighted_quantile_and_gate(self):
        self.assertEqual(study.weighted_quantile(
            np.array([1., 2., 9.]), np.array([8., 1., 1.])), 2.)
        def metrics(loss, coverage=.9):
            return {"absolute_token_pinball": loss, "coverage": coverage}
        assessment = {
            "equal_day_mean_absolute_token_pinball": {
                "relative_q90_hgb": 90.,
                **{name: 100. for name in study.BASELINES}},
            "support_fraction_each_day_at_least_0_95": True,
            "per_day": {str(i): {"methods": {
                "relative_q90_hgb": metrics(90.),
                **{name: metrics(100.) for name in study.BASELINES}}}
                        for i in range(3)},
        }
        self.assertTrue(study.assessment_gate(assessment)["all_passed"])
        assessment["equal_day_mean_absolute_token_pinball"][study.BASELINES[-1]] = 94.
        self.assertIn("mean_pinball_5pct_below_" + study.BASELINES[-1],
                      study.assessment_gate(assessment)["failed"])

    def test_bad_request_rows_and_incomplete_grid_are_rejected(self):
        cases = (
            ["2024-05-10 00:05:00+00:00,10", "2024-05-10 00:00:00+00:00,20"],
            ["2024-05-09 23:59:59+00:00,10"],
            ["2024-05-17 00:00:00+00:00,10"],
            ["2024-05-10 00:00:00+00:00,0"],
            ["2024-05-10 00:00:00+00:00,-1"],
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.csv"
            for rows in cases:
                source.write_text("TIMESTAMP,ContextTokens\n" + "\n".join(rows) + "\n")
                with self.assertRaises(ValueError):
                    study.aggregate_source(source)
        frame = study.design_from_aggregates(
            np.full(study.N_SLOTS, 2_000., dtype=float))
        counts = np.ones(study.N_SLOTS, dtype=np.int64)
        counts[0] += study.EXPECTED_ROWS - study.N_SLOTS
        counts[100] = 0
        counts[0] += 1
        with self.assertRaisesRegex(ValueError, "grid incomplete"):
            study.coding_support(frame, counts, study.EXPECTED_ROWS)

    def test_conversation_start_weekend_and_previous_day_fallback(self):
        class ConstantModel:
            def predict(self, inputs):
                return np.ones(len(inputs), dtype=float)

        y = np.full(study.N_SLOTS, 2_000., dtype=float)
        conv_start = study.START + np.timedelta64(2, "D")
        frame = study.design_from_aggregates(y, start=conv_start)
        self.assertEqual(float(frame.loc[12, "weekend"]), 1.)
        self.assertEqual(float(frame.loc[288, "weekend"]), 0.)
        fitted = {"model": ConstantModel(), "feature_names": study.FEATURES,
                  "global_ratio_q90": 2.,
                  "previous_day_ratio_residual_q90": 10.}
        forecasts = study.predict_from_bundle(fitted, frame)
        self.assertEqual(float(forecasts["previous_day_ratio"][12]), 4_000.)
        self.assertEqual(float(forecasts[
            "previous_day_ratio_plus_train_residual_q90"][12]), 4_000.)
        self.assertEqual(float(forecasts[
            "previous_day_ratio_plus_train_residual_q90"][300]), 22_000.)

    def test_eval_day_allows_at_most_fourteen_ineligible_bins(self):
        frame = study.design_from_aggregates(
            np.full(study.N_SLOTS, 2_000., dtype=float))
        counts = np.ones(study.N_SLOTS, dtype=np.int64)
        counts[0] += study.EXPECTED_ROWS - study.N_SLOTS
        self.assertTrue(study.coding_support(frame, counts, study.EXPECTED_ROWS)[
            "all_passed"])
        frame.loc[3 * 288:3 * 288 + 13, "scale"] = 999.
        self.assertTrue(study.coding_support(frame, counts, study.EXPECTED_ROWS)[
            "all_passed"])
        frame.loc[3 * 288 + 14, "scale"] = 999.
        self.assertFalse(study.coding_support(frame, counts, study.EXPECTED_ROWS)[
            "all_passed"])

    def test_scoring_uses_source_start_for_date_and_absolute_loss(self):
        y = np.full(study.N_SLOTS, 2_000., dtype=float)
        conv_start = study.START + np.timedelta64(2, "D")
        frame = study.design_from_aggregates(y, start=conv_start)
        forecasts = {name: np.full(study.N_SLOTS, 2_000., dtype=float)
                     for name in study.METHODS}
        result = study.score_period(frame, forecasts, 2, 2, start=conv_start)
        self.assertEqual(list(result["per_day"]), ["2024-05-14"])
        self.assertEqual(result["per_day"]["2024-05-14"]["eligible_bins"], 288)
        self.assertEqual(result["equal_day_mean_absolute_token_pinball"][
            "relative_q90_hgb"], 0.)


if __name__ == "__main__":
    unittest.main()
