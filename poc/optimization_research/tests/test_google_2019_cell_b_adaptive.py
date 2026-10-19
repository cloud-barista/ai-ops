"""Check frozen transfer's online boundary and pre-score gate."""

import json

import numpy as np
import pytest

from scripts import evaluate_google_2019_cell_b_adaptive as study


class LagModel:
    def predict(self, features):
        return np.asarray(features)[:, 0] * 0.5


class ConstantModel:
    def predict(self, features):
        return np.full(len(features), 50.0)


def synthetic_series():
    counts = [100 + (index % 24) for index in range(study.MIN_FULL_BINS)]
    return {"target": "synthetic", "first_full_bin": 2,
            "last_full_bin": 1 + len(counts), "counts": counts, "audit": {}}


def test_online_forecast_uses_only_closed_bins():
    first = synthetic_series()
    _, before = study.score_series(first, LagModel())
    changed = synthetic_series()
    changed["counts"][500] = 900
    _, after = study.score_series(changed, LagModel())
    at_target = 500 - study.BURN_IN
    assert before[at_target]["actual"] != after[at_target]["actual"]
    for method in study.METHODS:
        assert before[at_target][method] == after[at_target][method]
    assert before[at_target + 1]["mean_12"] != after[at_target + 1]["mean_12"]


def test_constant_learned_shape_reduces_to_recent_mean():
    _, rows = study.score_series(synthetic_series(), ConstantModel())
    assert all(row["adaptive_hgb"] == pytest.approx(row["mean_12"]) for row in rows)


def test_incomplete_series_is_rejected():
    series = synthetic_series()
    series["counts"].pop()
    series["last_full_bin"] -= 1
    with pytest.raises(ValueError, match="incomplete"):
        study.score_series(series, ConstantModel())


def test_scoring_refuses_missing_or_changed_freeze(monkeypatch, tmp_path):
    missing = tmp_path / "freeze.json"
    monkeypatch.setattr(study, "FREEZE", missing)
    with pytest.raises(ValueError, match="freeze missing"):
        study.verify_freeze()
    missing.write_text(json.dumps({"script_sha256": "0" * 64}))
    monkeypatch.setattr(study, "freeze_payload", lambda: {"script_sha256": "1" * 64})
    with pytest.raises(ValueError, match="no longer matches"):
        study.verify_freeze()
