"""Behavioral checks for Philly risk features, fitting and simple baselines."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.philly_risk_models import (
    BASELINE_KEYS, FeatureTransform, fit_beta_table, select_beta_baseline,
    select_logistic,
)


def row(old: float | None, middle: float | None, current: float,
        y: int | None) -> dict:
    old_valid, middle_valid = old is not None, middle is not None
    result = {
        "older_util": old, "middle_util": middle, "current_util": current,
        "older_valid": old_valid, "middle_valid": middle_valid,
        "past_gap_s": 300 if middle_valid else 600 if old_valid else 900,
        "current_high": current >= 80,
        "any_recent_high": (current >= 80 or (old_valid and old >= 80)
                            or (middle_valid and middle >= 80)),
    }
    if y is not None:
        result["y"] = y
    return result


class PhillyRiskModelsTest(unittest.TestCase):
    def test_training_medians_are_reused_for_missing_past_values(self):
        train = pd.DataFrame([
            row(10, 20, 30, 0), row(50, 60, 35, 0),
            row(None, 40, 25, 1), row(30, None, 45, 1),
        ])
        transform = FeatureTransform(include_quality=True).fit(train)
        self.assertEqual(transform.older_median, 30.0)
        self.assertEqual(transform.middle_median, 40.0)
        unseen = pd.DataFrame([row(None, None, 15, None)])
        engineered = transform._raw_features(unseen)
        self.assertEqual(engineered[0, 0], 30.0)
        self.assertEqual(engineered[0, 1], 40.0)
        self.assertEqual(engineered[0, 3], (30 + 40 + 15) / 3)
        self.assertEqual(engineered[0, -1], 900)
        self.assertEqual(transform.transform(unseen).shape, (1, 11))

    def test_beta_rates_are_cohort_specific_and_unseen_cells_default_half(self):
        complete = pd.DataFrame([row(10, 20, 30, 0) for _ in range(8)]
                                + [row(90, 20, 30, 1) for _ in range(2)])
        partial = pd.DataFrame([row(None, 20, 30, 1) for _ in range(8)]
                               + [row(None, 20, 30, 0) for _ in range(2)])
        complete_table = fit_beta_table(complete, "prevalence", ())
        partial_table = fit_beta_table(partial, "prevalence", ())
        self.assertAlmostEqual(complete_table.predict(complete[:1])[0], 3 / 12)
        self.assertAlmostEqual(partial_table.predict(partial[:1])[0], 9 / 12)
        recent = fit_beta_table(complete, "state_recent",
                                ("current_high", "any_recent_high"))
        unseen = pd.DataFrame([row(10, 20, 95, None)])
        self.assertEqual(recent.predict(unseen)[0], 0.5)
        selected, scores = select_beta_baseline(complete, complete)
        self.assertEqual(set(scores), {name for name, _ in BASELINE_KEYS})
        self.assertIn(selected.name, scores)

    def test_logistic_predicts_without_future_label_or_observability_columns(self):
        train = pd.DataFrame([
            row(5 + i * 2, 10 + i * 2, 15 + i * 2, int(i % 4 >= 2))
            for i in range(24)
        ])
        validation = pd.DataFrame([
            row(12 + i * 3, 20 + i * 2, 25 + i, int(i % 3 == 0))
            for i in range(9)
        ])
        fitted = select_logistic(train, validation, include_quality=True)
        probabilities = fitted.predict(validation.drop(columns=["y"]))
        self.assertEqual(probabilities.shape, (9,))
        self.assertTrue(np.isfinite(probabilities).all())
        self.assertTrue(((0 <= probabilities) & (probabilities <= 1)).all())
        self.assertIn(fitted.c, (0.1, 1.0, 10.0))

    def test_inconsistent_mask_or_threshold_is_rejected(self):
        train = pd.DataFrame([
            row(10, 20, 30, 0), row(90, 20, 40, 1),
            row(10, 30, 50, 0), row(90, 30, 60, 1),
        ])
        broken = train.copy()
        broken.loc[0, "any_recent_high"] = True
        with self.assertRaisesRegex(ValueError, "any_recent_high"):
            FeatureTransform(True).fit(broken)
        broken = train.copy()
        broken.loc[0, "past_gap_s"] = 600
        with self.assertRaisesRegex(ValueError, "past_gap_s"):
            FeatureTransform(True).fit(broken)


if __name__ == "__main__":
    unittest.main()
