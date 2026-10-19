"""Synthetic-only blocked calibration, boundary, and freeze tests."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_burstgpt_input_demand_v2 as study


def test_fit_calibration_and_validation_blocks_are_disjoint_and_mature():
    fit, cal, val = study.split_blocks()
    assert (fit[0], fit[-1], len(fit)) == (5425, 6118, 694)
    assert (cal[0], cal[-1], len(cal)) == (6120, 6358, 239)
    assert (val[0], val[-1], len(val)) == (6360, 6839, 480)
    assert fit[-1] + 2 <= cal[0] and cal[-1] + 2 <= val[0]
    assert 6119 not in fit and 6359 not in cal


def test_frozen_h_minus_two_features_do_not_use_h_minus_one_or_current():
    volume = np.arange(1, 70, dtype=np.int64)
    count = np.ones(69, dtype=np.int64)
    hour = study.v1.START_H + 25
    before = study.v1.feature_row(volume, count, h=hour)
    changed_v, changed_c = volume.copy(), count.copy()
    changed_v[24:] = 9_999_999
    changed_c[24:] = 9_999_999
    np.testing.assert_array_equal(before, study.v1.feature_row(changed_v, changed_c, h=hour))
    changed_v[23] = 0
    assert not np.array_equal(before, study.v1.feature_row(changed_v, changed_c, h=hour))


def test_one_sided_calibration_uses_216th_ascending_and_never_lowers_prediction():
    delta, rank = study.calibration_delta(np.arange(239, dtype=float)[::-1])
    assert rank == 216 and delta == 215
    assert study.calibration_delta(-np.arange(1, 240, dtype=float))[0] == 0
    with pytest.raises(ValueError, match="frozen finite"):
        study.calibration_delta(np.array([1, 2, np.nan]), expected_n=3)
    with pytest.raises(ValueError, match="frozen finite"):
        study.calibration_delta(np.arange(238))


def test_block_support_requires_logged_hours_in_both_fit_and_calibration():
    hourly = study.v1.empty_hourly(study.v1.START_H, study.v1.TEST_START_H)
    for cohort in study.v1.COHORTS:
        hourly[cohort]["count"][:] = 1
    summary = study.block_support(hourly, "frozen")
    assert summary["all_pass"]
    assert summary["cohorts"]["GPT-4"]["fit"]["hours"] == 720
    assert summary["cohorts"]["GPT-4"]["calibration"]["hours"] == 240
    ix = study.CAL_START_H - study.v1.START_H
    hourly["GPT-4"]["count"][ix:ix + 240] = 0
    assert not study.block_support(hourly, "frozen")["all_pass"]


def test_fit_does_not_use_unmatured_boundary_or_calibration_label(monkeypatch):
    fitted = []
    class RecordingModel:
        def __init__(self, **params):
            self.params = params
        def fit(self, x, y):
            fitted.append((self.params, x.copy(), y.copy()))
            return self
        def predict(self, x):
            return np.zeros(len(x), dtype=float)
    monkeypatch.setattr(study, "HistGradientBoostingRegressor", RecordingModel)
    hourly = study.v1.empty_hourly(study.v1.START_H, study.v1.TEST_START_H)
    fit, cal, _ = study.split_blocks()
    for cohort in study.v1.COHORTS:
        data = hourly[cohort]
        data["count"][:] = 1
        data["volume"][fit - study.v1.START_H] = 3
        data["volume"][6119 - study.v1.START_H] = 1_000_000
        data["volume"][cal - study.v1.START_H] = 100
        data["volume"][study.VAL_START_H - study.v1.START_H:] = 10_000_000
    _, deltas, diagnostics = study.fit_and_calibrate(hourly, fit, cal)
    assert len(fitted) == 2
    assert all(len(y) == 694 and np.max(y) == 3 for _, _, y in fitted)
    assert all(params["loss"] == "quantile" and params["quantile"] == 0.9
               for params, _, _ in fitted)
    assert deltas == {"ChatGPT": 100.0, "GPT-4": 100.0}
    assert all(item["calibration_rank_1_based"] == 216 for item in diagnostics.values())


def _write_v1_rows(path: Path, rows: list[dict]) -> None:
    columns = ("cohort", "relative_hour", "relative_day", "recorded_request_count",
               "recorded_input_tokens", *study.v1.BASELINES, "quantile_hgb")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_v1_frozen_predictions_must_match_source_target_count_and_exact_keys(tmp_path, monkeypatch):
    source = study.v1.empty_hourly(study.v1.START_H, study.v1.TEST_START_H)
    hour = study.VAL_START_H
    for cohort in study.v1.COHORTS:
        source[cohort]["count"][hour - study.v1.START_H] = 2
        source[cohort]["volume"][hour - study.v1.START_H] = 5
    rows = [{"cohort": cohort, "relative_hour": hour, "relative_day": hour // 24,
             "recorded_request_count": 2, "recorded_input_tokens": 5,
             **{name: 10 for name in study.v1.BASELINES}, "quantile_hgb": 0}
            for cohort in study.v1.COHORTS]
    file = tmp_path / "v1_predictions.csv"
    monkeypatch.setattr(study.v1, "PREDICTIONS", file)
    _write_v1_rows(file, rows)
    assert len(study.read_frozen_v1_validation(source, np.array([hour]))) == 2
    rows[0]["recorded_input_tokens"] = 6
    _write_v1_rows(file, rows)
    with pytest.raises(ValueError, match="target/count"):
        study.read_frozen_v1_validation(source, np.array([hour]))
    rows[0]["recorded_input_tokens"] = 5
    _write_v1_rows(file, rows[:1])
    with pytest.raises(ValueError, match="incomplete"):
        study.read_frozen_v1_validation(source, np.array([hour]))


def test_same_row_validation_gates_match_v1_and_report_ablation():
    class ZeroModel:
        def predict(self, x):
            return np.zeros(len(x), dtype=float)
    hourly = study.v1.empty_hourly(study.v1.START_H, study.v1.TEST_START_H)
    fit, cal, val = study.split_blocks()
    old_rows = {}
    for cohort in study.v1.COHORTS:
        hourly[cohort]["volume"][val - study.v1.START_H] = 5
        for h in val:
            old_rows[(cohort, int(h))] = {
                "recorded_request_count": 1,
                "recorded_input_tokens": 5,
                **{name: 10.0 for name in study.v1.BASELINES},
                "quantile_hgb": 0.0,
            }
    prior_by_cohort = {cohort: {
        **{name: {"pinball": 0.5, "coverage": 1.0, "mean_prediction": 10.0}
           for name in study.v1.BASELINES},
        "quantile_hgb": {"pinball": 4.5, "coverage": 0.0, "mean_prediction": 0.0},
    } for cohort in study.v1.COHORTS}
    metrics = {"selected_baseline": "train_fixed_p90", "by_cohort": prior_by_cohort}
    models = {cohort: ZeroModel() for cohort in study.v1.COHORTS}
    deltas = {cohort: 5.0 for cohort in study.v1.COHORTS}
    scores, rows = study.score_validation(hourly, models, deltas, val, old_rows, metrics)
    assert len(rows) == 960
    assert scores["selected_baseline"] == "train_fixed_p90"
    assert scores["adoption_gates"]["all_pass"]
    assert scores["daily_wins_against_selected_baseline"] == 20
    assert scores["by_cohort"]["ChatGPT"][study.RAW_NAME]["coverage"] == 0
    assert scores["by_cohort"]["ChatGPT"][study.MODEL_NAME]["coverage"] == 1


def test_nonfinite_new_predictions_fail_before_any_validation_artifact():
    class NonfiniteModel:
        def predict(self, x):
            return np.full(len(x), np.nan)
    hourly = study.v1.empty_hourly(study.v1.START_H, study.v1.TEST_START_H)
    models = {cohort: NonfiniteModel() for cohort in study.v1.COHORTS}
    deltas = {cohort: 0.0 for cohort in study.v1.COHORTS}
    with pytest.raises(ValueError, match="finite and nonnegative"):
        study.score_validation(hourly, models, deltas,
                               np.array([study.VAL_START_H]), {}, {})


def test_freeze_hashes_only_and_refuses_to_overwrite(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_text("not parsed during freeze", encoding="utf-8")
    monkeypatch.setattr(study.v1, "SOURCE", source)
    monkeypatch.setattr(study.v1, "SOURCE_BYTES", source.stat().st_size)
    monkeypatch.setattr(study.v1, "SOURCE_SHA256", study.v1.sha256_file(source))
    audit, protocol, code, tests = [tmp_path / name for name in
                                    ("audit.json", "protocol.md", "code.py", "tests.py")]
    for file in (audit, protocol, code, tests):
        file.write_text("frozen input", encoding="utf-8")
    monkeypatch.setattr(study.v1, "SOURCE_AUDIT", audit)
    monkeypatch.setattr(study, "PROTOCOL", protocol)
    monkeypatch.setattr(study, "SCRIPT", code)
    monkeypatch.setattr(study, "TESTS", tests)
    old = tmp_path / "old.txt"
    old.write_text("v1 immutable", encoding="utf-8")
    monkeypatch.setattr(study, "V1_FILES", {"v1_predictions_sha256": old})
    output = tmp_path / "v2"
    monkeypatch.setattr(study, "OUT", output)
    for name, leaf in (("FREEZE", "freeze.json"), ("SUPPORT", "support.json"),
                       ("METRICS", "metrics.json"), ("MODELS", "model.joblib"),
                       ("PREDICTIONS", "predictions.csv")):
        monkeypatch.setattr(study, name, output / leaf)
    frozen = study.freeze()
    assert frozen["source_sha256"] == study.v1.sha256_file(source)
    assert frozen["v1_predictions_sha256"] == study.v1.sha256_file(old)
    assert old.read_text(encoding="utf-8") == "v1 immutable"
    with pytest.raises(FileExistsError, match="overwrite"):
        study.freeze()
    reviewed_sha = study.v1.sha256_file(study.FREEZE)
    code.write_text("tampered after freeze", encoding="utf-8")
    with pytest.raises(ValueError, match="script_sha256"):
        study.verify_freeze(reviewed_sha)


def test_score_cannot_start_without_reviewed_manifest_digest(monkeypatch, tmp_path):
    for name, leaf in (("SUPPORT", "support.json"), ("METRICS", "metrics.json"),
                       ("MODELS", "model.joblib"), ("PREDICTIONS", "prediction.csv")):
        monkeypatch.setattr(study, name, tmp_path / leaf)
    def forbidden(*args, **kwargs):
        raise AssertionError("Source parser reached before reviewed freeze")
    monkeypatch.setattr(study.v1, "load_preperiod", forbidden)
    with pytest.raises(ValueError, match="SHA256"):
        study.select("invalid")
