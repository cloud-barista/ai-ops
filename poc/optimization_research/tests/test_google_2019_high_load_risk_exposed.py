"""Synthetic checks for the exposed-cell high-load risk development audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_google_2019_high_load_risk_exposed.py"
spec = importlib.util.spec_from_file_location("google_2019_high_load_risk_exposed", SCRIPT)
study = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(study)


def test_threshold_uses_only_first_300_bins_and_closed_12_bin_means() -> None:
    counts = np.arange(1000, dtype=float)
    expected = float(np.quantile(
        np.convolve(counts[:300], np.ones(12) / 12, mode="valid"),
        .9, method="higher"))
    assert study.burn_in_threshold(counts) == expected
    changed = counts.copy()
    changed[300:] = 1_000_000
    assert study.burn_in_threshold(changed) == expected


def test_features_and_baselines_do_not_read_current_or_future_bins() -> None:
    counts = 20 + np.arange(900, dtype=float) % 17
    target = 864
    threshold = study.burn_in_threshold(counts)
    current_features, current_baselines = study.one_feature(counts, target, threshold)
    changed = counts.copy()
    changed[target:] = 1_000_000
    next_features, next_baselines = study.one_feature(changed, target, threshold)
    np.testing.assert_array_equal(current_features, next_features)
    assert current_baselines == next_baselines
    assert np.mean(changed[target:target + 12]) > np.mean(counts[target:target + 12])


def test_future_hour_label_matures_after_12_full_bins() -> None:
    counts = np.full(900, 20.0)
    counts[:300] = 20 + np.arange(300) % 17
    start = 864
    original = study.make_examples(counts)
    changed_after_maturity = counts.copy()
    changed_after_maturity[start + study.HORIZON:] = 1_000_000
    next_example = study.make_examples(changed_after_maturity)
    index = start - study.BURN_IN
    assert original["labels"][index] == next_example["labels"][index]
    np.testing.assert_array_equal(original["feature_matrix"][index],
                                  next_example["feature_matrix"][index])
    changed_within_target = counts.copy()
    changed_within_target[start + study.HORIZON - 1] = 1_000_000
    target_changed = study.make_examples(changed_within_target)
    assert original["labels"][index] != target_changed["labels"][index]
    np.testing.assert_array_equal(original["feature_matrix"][index],
                                  target_changed["feature_matrix"][index])


def test_alert_cutoff_uses_only_prior_scores_and_higher_quantile() -> None:
    scores = np.arange(600, dtype=float)
    scores[564] = 506
    original = study.causal_alerts(scores)
    assert not original[564]  # Higher 90th percentile of scores 0..563 is 507.
    scores[564] = 507
    on_cutoff = study.causal_alerts(scores)
    assert on_cutoff[564]
    scores[565:] = -1_000_000
    changed_future = study.causal_alerts(scores)
    assert changed_future[564]
