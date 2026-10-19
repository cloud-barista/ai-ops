"""Check the held-out seasonal method's causal boundary and freeze gate."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import evaluate_google_2019_cell_g_seasonal as study


def synthetic_series():
    counts = [100 + index % 24 for index in range(study.MIN_FULL_BINS)]
    return {"target": "synthetic", "first_full_bin": 2,
            "last_full_bin": 1 + len(counts), "counts": counts, "audit": {}}


def test_seasonal_residual_uses_closed_bins_only():
    original = synthetic_series()
    _, before = study.score_series(original, .5)
    edited = synthetic_series()
    edited["counts"][500] = 900
    _, after = study.score_series(edited, .5)
    at_target = 500 - study.BURN_IN
    assert before[at_target]["actual"] != after[at_target]["actual"]
    for name in study.METHODS:
        assert before[at_target][name] == after[at_target][name]
    assert after[at_target + 1]["seasonal_residual"] != before[at_target + 1]["seasonal_residual"]


def test_alpha_training_selects_smallest_tie(monkeypatch):
    monkeypatch.setattr(study, "design", lambda _: (
        np.array([2.]), np.array([1.]), np.array([1.]), np.array([0.])))
    alpha, training = study.train_alpha({}, {})
    assert alpha == 0.0
    assert training["selected"]["equal_cell_mean_mae_ratio"] == pytest.approx(1.0)


def test_alpha_training_weights_cells_equally_despite_scale(monkeypatch):
    monkeypatch.setattr(study, "ALPHAS", (0.0, 1.0))
    def data(series):
        if series == "large":
            return np.array([100.]), np.array([0.]), np.array([0.]), np.array([100.])
        return np.array([0.]), np.array([1.]), np.array([0.]), np.array([1.])
    monkeypatch.setattr(study, "design", data)
    alpha, training = study.train_alpha("large", "small")
    assert alpha == 0.0
    assert training["grid_scores"][1]["cell_mae_ratio"] == {
        "cell_a_clean": 0.0, "cell_b": 2.0}


def test_malformed_series_and_alpha_are_rejected():
    series = synthetic_series()
    series["counts"][100] = -1
    with pytest.raises(ValueError, match="malformed"):
        study.score_series(series, .5)
    with pytest.raises(ValueError, match="alpha"):
        study.score_series(synthetic_series(), .51)


def test_new_freeze_refuses_existing_heldout_content(monkeypatch, tmp_path):
    heldout = tmp_path / "heldout"
    heldout.mkdir()
    (heldout / "collection_events-000000000000.json.gz").write_bytes(b"already opened")
    monkeypatch.setattr(study, "OUTPUT", tmp_path)
    monkeypatch.setattr(study, "FREEZE", tmp_path / "freeze.json")
    monkeypatch.setattr(study, "source", SimpleNamespace(SOURCE=heldout,
                                                        VERIFIED=heldout / "verified.json"))
    monkeypatch.setattr(study, "freeze_payload", lambda: {"alpha": .5})
    with pytest.raises(ValueError, match="event content exists"):
        study.freeze()
    assert not (tmp_path / "freeze.json").exists()


def test_scoring_rejects_changed_freeze(monkeypatch, tmp_path):
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"alpha": .5}))
    monkeypatch.setattr(study, "FREEZE", freeze)
    monkeypatch.setattr(study, "freeze_payload", lambda: {"alpha": .75})
    with pytest.raises(ValueError, match="differs"):
        study.verify_freeze()
