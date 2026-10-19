"""Causal selection and integrity checks for the new cell-e holdout."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import evaluate_google_2019_cell_e_selector as study


def synthetic_series():
    counts = [100 + index % 24 for index in range(study.MIN_FULL_BINS)]
    return {"target": "synthetic", "first_full_bin": 2,
            "last_full_bin": 1 + len(counts), "counts": counts, "audit": {}}


def test_first_test_target_cannot_change_selection_or_its_forecast():
    original = synthetic_series()
    before, rows_before = study.score_series(original, .85)
    edited = synthetic_series()
    edited["counts"][study.SELECT_END] = 900
    after, rows_after = study.score_series(edited, .85)
    assert before["selection"] == after["selection"]
    for name in study.METHODS:
        assert rows_before[0][name] == rows_after[0][name]
    assert rows_before[0]["actual"] != rows_after[0]["actual"]
    assert rows_before[1]["mean_12"] != rows_after[1]["mean_12"]


def test_calibration_selects_seasonal_only_with_five_percent_margin():
    y = np.zeros(study.MIN_FULL_BINS)
    offsets = np.arange(study.BURN_IN, study.MIN_FULL_BINS)
    predictions = {"mean_12": np.full(len(offsets), 10.),
                   "seasonal_residual": np.zeros(len(offsets))}
    choice, evidence = study.select_expert(y, offsets, predictions)
    assert choice == "seasonal_residual"
    assert evidence["bins"] == 564
    predictions["seasonal_residual"][:] = 10.
    choice, _ = study.select_expert(y, offsets, predictions)
    assert choice == "mean_12"


def test_malformed_series_and_coefficient_fail_closed():
    series = synthetic_series()
    series["counts"][100] = -1
    with pytest.raises(ValueError, match="malformed"):
        study.score_series(series, .85)
    with pytest.raises(ValueError, match="coefficient"):
        study.score_series(synthetic_series(), .8)


def test_new_freeze_refuses_heldout_content(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "collection_events-000000000000.json.gz").write_bytes(b"already opened")
    monkeypatch.setattr(study, "OUTPUT", tmp_path)
    monkeypatch.setattr(study, "FREEZE", tmp_path / "freeze.json")
    monkeypatch.setattr(study, "source", SimpleNamespace(SOURCE=source,
                                                        VERIFIED=source / "verified.json"))
    monkeypatch.setattr(study, "freeze_payload", lambda: {"alpha": .85})
    with pytest.raises(ValueError, match="event content exists"):
        study.freeze()
    assert not (tmp_path / "freeze.json").exists()


def test_changed_freeze_is_rejected(monkeypatch, tmp_path):
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"alpha": .85}))
    monkeypatch.setattr(study, "FREEZE", freeze)
    monkeypatch.setattr(study, "freeze_payload", lambda: {"alpha": 0.1})
    with pytest.raises(ValueError, match="differs"):
        study.verify_freeze()
