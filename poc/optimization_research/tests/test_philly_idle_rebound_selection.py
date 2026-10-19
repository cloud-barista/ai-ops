"""Synthetic training-only controls for the Philly idle-rebound selection."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_philly_idle_rebound as audit
import select_philly_idle_rebound as study
import select_philly_idle_rebound_v2 as study_v2


def test_feature_window_never_reads_unavailable_or_future_values():
    start = audit.SPLITS[0][1]
    grid = np.arange(start - 300, start - 300 + 320 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    i = 288
    peak[i - 20] = 70
    peak[i - 1:i + 1] = 2
    decision = int(grid[i]) + 600
    first = study.feature_and_groups(grid, peak, valid, i, decision, 8)
    peak[i + 1:] = 99
    valid[i + 1:] = False
    second = study.feature_and_groups(grid, peak, valid, i, decision, 8)
    np.testing.assert_array_equal(first[0], second[0])
    assert first[1:] == second[1:]
    assert first[1] == "high"
    assert first[2] == "at_most_2h"


def test_partial_24h_history_uses_missing_fraction_not_zero_observations():
    start = audit.SPLITS[0][1]
    grid = np.arange(start - 300, start - 300 + 16 * 300, 300)
    peak = np.full(len(grid), 4.0)
    valid = np.ones(len(grid), dtype=bool)
    i = 5
    decision = int(grid[i]) + 600
    features, twohour, age = study.feature_and_groups(grid, peak, valid, i, decision, 2)
    observed = dict(zip(study.FEATURE_NAMES, features))
    assert observed["past_2h_valid_fraction"] == pytest.approx(6 / 24)
    assert observed["past_24h_valid_fraction"] == pytest.approx(6 / 288)
    assert observed["past_24h_mean"] == 4
    assert observed["no_observed_high_24h"] == 1
    assert twohour == "unknown"
    assert age == "no_observed_high"


def test_beta_rates_use_training_labels_and_unseen_cell_falls_back_to_prior():
    train = pd.DataFrame({
        "rebound": [0, 1, 0, 0],
        "recent_2h_group": ["high", "high", "low", "low"],
        "age_24h_group": ["at_most_2h", "at_most_2h", "over_2_to_6h", "over_2_to_6h"],
    })
    validation = pd.DataFrame({
        "rebound": [1, 1],  # prediction must not consume these outcomes
        "recent_2h_group": ["high", "unknown"],
        "age_24h_group": ["at_most_2h", "no_observed_high"],
    })
    spec = study.fit_beta_baselines(train)
    first = study.predict_beta_baselines(spec, validation)
    validation["rebound"] = [0, 0]
    second = study.predict_beta_baselines(spec, validation)
    for key in first:
        np.testing.assert_array_equal(first[key], second[key])
    assert first["recent_2h_beta"][1] == pytest.approx(spec["train_beta"])
    assert first["age_24h_beta"][1] == pytest.approx(spec["train_beta"])


def test_development_loader_filters_december_before_feature_construction(monkeypatch):
    class ReaderReached(Exception):
        pass

    def fake_read(path, *, columns, filters):
        assert filters == [("time_utc_s", "<", audit.SPLITS[1][2])]
        assert "time_utc_s" in columns and "gpu0_util" in columns
        raise ReaderReached

    monkeypatch.setattr(study.pd, "read_parquet", fake_read)
    with pytest.raises(ReaderReached):
        study.build_train_validation(Path("never_opened.parquet"))


def test_logistic_selection_reports_only_train_and_validation():
    rng = np.random.default_rng(42)
    n_train, n_val = 100, 40
    x = rng.normal(size=(n_train + n_val, len(study.FEATURE_NAMES)))
    y = np.r_[rng.integers(0, 2, n_train), rng.integers(0, 2, n_val)]
    data = pd.DataFrame(x, columns=study.FEATURE_NAMES)
    data["rebound"] = y
    data["recent_2h_group"] = np.where(x[:, 0] > 0, "high", "low")
    data["age_24h_group"] = np.where(x[:, 1] > 0, "at_most_2h", "over_2_to_6h")
    selected, metrics = study.select_from_validation(data.iloc[:n_train], data.iloc[n_train:])
    assert metrics["train_n"] == n_train and metrics["validation_n"] == n_val
    assert set(metrics["model_validation_brier_by_c"]) == set(study.C_ORDER)
    assert set(metrics["baseline_validation_brier"]) == set(study.BASELINE_ORDER)
    assert selected["selected_c"] in study.C_ORDER
    assert "test" not in str(metrics).lower()


def test_v2_rejects_nonconverged_candidate_before_validation_scoring(monkeypatch):
    class StalledLogistic:
        def __init__(self, **kwargs):
            self.max_iter = kwargs["max_iter"]

        def fit(self, x, y):
            self.n_iter_ = np.array([self.max_iter])
            return self

        def predict_proba(self, x):
            raise AssertionError("Nonconverged model must never score validation")

    monkeypatch.setattr(study_v2, "LogisticRegression", StalledLogistic)
    x = np.zeros((6, len(study_v2.FEATURE_NAMES)))
    train = pd.DataFrame(x[:4], columns=study_v2.FEATURE_NAMES)
    validation = pd.DataFrame(x[4:], columns=study_v2.FEATURE_NAMES)
    train["rebound"] = [0, 1, 0, 1]
    validation["rebound"] = [0, 1]
    for frame in (train, validation):
        frame["recent_2h_group"] = "low"
        frame["age_24h_group"] = "no_observed_high"
    with pytest.raises(RuntimeError, match="did not converge"):
        study_v2.select_from_validation(train, validation)
