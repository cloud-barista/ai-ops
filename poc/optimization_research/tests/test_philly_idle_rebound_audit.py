"""Boundary and missingness tests for the score-free Philly rebound audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_philly_idle_rebound as study


def _minute_frame() -> pd.DataFrame:
    rows = []
    for minute, first, second in ((0, 8.0, 2.0), (60, 6.0, 4.0),
                                  (120, 7.0, 3.0), (180, 9.0, 4.0),
                                  (240, 10.0, 5.0)):
        rows.append({"machine_id": "m0", "time_utc_s": minute,
                     "expected_gpu_slots": 2, "width_matches_first": True,
                     "row_usable": True, "gpu0_util": first,
                     "gpu1_util": second,
                     **{f"gpu{i}_util": np.nan for i in range(2, 8)}})
    return pd.DataFrame(rows)


def test_machine_window_uses_all_expected_slots_and_five_unique_minutes():
    frame = _minute_frame()
    grid, peak, valid = study.windows_for_machine(frame)
    assert grid.tolist() == [0]
    assert valid.tolist() == [True]
    assert peak[0] == pytest.approx(8.0)

    missing_gpu = frame.copy()
    missing_gpu.loc[0, "gpu1_util"] = np.nan
    assert study.windows_for_machine(missing_gpu)[2].tolist() == [False]

    duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    assert study.windows_for_machine(duplicated)[2].tolist() == [False]

    wrong_minute = frame.copy()
    wrong_minute.loc[2, "time_utc_s"] = 121
    assert study.windows_for_machine(wrong_minute)[2].tolist() == [False]


def test_decision_lag_excludes_unavailable_gap_and_starts_future_at_decision():
    start = study.SPLITS[0][1]
    grid = np.arange(start - 300, start - 300 + 50 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    peak[6] = 99.0  # [d-300,d): window is unavailable at decision d
    peak[7] = 55.0  # [d,d+300): first label window
    rows = list(study.iter_decisions("m0", grid, peak, valid))
    first = next(r for r in rows if r["decision_utc_s"] == start + 1_800)
    assert first["rebound"] is True
    assert first["history_complete"] is False
    assert first["history_valid_count"] == 6

    peak[7] = 0.0
    rows = list(study.iter_decisions("m0", grid, peak, valid))
    first = next(r for r in rows if r["decision_utc_s"] == start + 1_800)
    assert first["rebound"] is False  # the unavailable gap at index 6 is ignored


def test_missing_future_is_unknown_and_partial_history_is_not_an_exclusion():
    start = study.SPLITS[0][1]
    grid = np.arange(start - 300, start - 300 + 50 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    valid[7] = False
    rows = list(study.iter_decisions("m0", grid, peak, valid))
    first = next(r for r in rows if r["decision_utc_s"] == start + 1_800)
    assert first["future_complete"] is False
    assert first["rebound"] is None
    assert first["history_complete"] is False


def test_short_future_at_machine_end_is_in_denominator_as_unknown():
    start = study.SPLITS[0][1]
    grid = np.arange(start - 300, start - 300 + 8 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    rows = list(study.iter_decisions("m0", grid, peak, valid))
    first = next(r for r in rows if r["decision_utc_s"] == start + 1_800)
    assert first["future_complete"] is False
    assert first["rebound"] is None


def test_labels_must_mature_before_split_end():
    _, train_start, train_end = study.SPLITS[0]
    assert study.split_at(train_start) == "train"
    assert study.split_at(train_end - 7_500) == "train"
    assert study.split_at(train_end - 7_499) is None
    assert study.split_at(train_end) == "validation"


def test_summary_keeps_machine_episode_and_history_support_separate():
    start = study.SPLITS[2][1]
    decisions = [
        {"split": "test", "machine_id": "m0", "decision_utc_s": start + delta,
         "future_complete": True, "rebound": rebound, "history_complete": complete,
         "history_valid_count": n}
        for delta, rebound, complete, n in
        ((0, True, True, 288), (1800, True, False, 280),
         (10800, True, False, 275), (12600, False, True, 288))
    ]
    decisions.append({"split": "test", "machine_id": "m1", "decision_utc_s": start,
                      "future_complete": False, "rebound": None,
                      "history_complete": False, "history_valid_count": 10})
    result = study.summarize_decisions(decisions, cohort="machine_holdout", source_sha256="x")
    info = result["splits"]["test"]
    assert info["idle_input_candidates"] == 5
    assert info["future_complete"] == 4
    assert info["positive_rebound"] == 3
    assert info["negative_rebound"] == 1
    assert info["positive_runs_gap_over_2h"] == 2
    assert info["history_24h_partial"] == 2
    assert set(result["splits"]) == {"test"}


def test_holdout_requires_frozen_selection_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(study, "verify_freeze", lambda _: {"protocol_sha256": "a" * 64})
    with pytest.raises(ValueError, match="stays locked"):
        study.audit("machine_holdout", tmp_path / "freeze.json")
    bad = tmp_path / "selection.json"
    bad.write_text(json.dumps({"protocol_sha256": "b" * 64,
                               "selected_model_sha256": "c" * 64,
                               "selected_baseline": "train_beta"}), encoding="utf-8")
    with pytest.raises(ValueError, match="leaves the frozen artifact directory"):
        study.audit("machine_holdout", tmp_path / "freeze.json", bad)


def test_holdout_manifest_checks_real_files_and_validation_choice(tmp_path: Path, monkeypatch):
    root = tmp_path / "research"
    artifact = root / "artifacts/philly_idle_rebound_v1"
    scripts = root / "scripts"
    artifact.mkdir(parents=True)
    scripts.mkdir()
    monkeypatch.setattr(study, "ROOT", root)
    development = artifact / "development_eligibility_v2.json"
    development.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(study, "DEVELOPMENT_ELIGIBILITY", development)
    frozen = artifact / "freeze.json"
    frozen.write_text("{}\n", encoding="utf-8")
    model = artifact / "model.joblib"
    model.write_bytes(b"frozen-model")
    code = scripts / "choose.py"
    code.write_text("# frozen selection\n", encoding="utf-8")
    protocol_sha = "a" * 64
    metrics = artifact / "validation.json"
    metrics.write_text(json.dumps({
        "kind": "philly_idle_rebound_validation_selection_v1",
        "protocol_sha256": protocol_sha,
        "development_source_sha256": study.SOURCE["development"]["sha256"],
        "development_eligibility_sha256": study.sha256_file(development),
        "selected_model_sha256": study.sha256_file(model),
        "selection_code_sha256": study.sha256_file(code),
        "selected_baseline": "age_24h_beta",
        "validation_n": 600,
        "baseline_validation_brier": {"train_beta": 0.2, "recent_2h_beta": 0.15,
                                      "age_24h_beta": 0.1},
        "model_validation_brier_by_c": {"0.1": 0.09, "1": 0.08, "10": 0.1},
        "selected_c": "1",
    }), encoding="utf-8")
    selection = artifact / "selection.json"
    payload = {
        "protocol_sha256": protocol_sha,
        "freeze_manifest_sha256": study.sha256_file(frozen),
        "development_source_sha256": study.SOURCE["development"]["sha256"],
        "development_eligibility_sha256": study.sha256_file(development),
        "selected_model_id": "l2_logistic", "selected_baseline": "age_24h_beta",
        "selected_c": "1",
        "selected_model_path": "artifacts/philly_idle_rebound_v1/model.joblib",
        "selected_model_sha256": study.sha256_file(model),
        "selection_code_path": "scripts/choose.py",
        "selection_code_sha256": study.sha256_file(code),
        "validation_metrics_path": "artifacts/philly_idle_rebound_v1/validation.json",
        "validation_metrics_sha256": study.sha256_file(metrics),
    }
    selection.write_text(json.dumps(payload), encoding="utf-8")
    study.verify_selection_for_holdout(selection, {"protocol_sha256": protocol_sha}, frozen)

    model.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash differs"):
        study.verify_selection_for_holdout(selection, {"protocol_sha256": protocol_sha}, frozen)
