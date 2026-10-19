"""Synthetic causal, boundary, support and reproducibility tests; no test data."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_burstgpt_input_demand as demand


def test_empty_logged_hour_differs_from_recorded_zero_token_request():
    values = demand.empty_hourly(demand.START_H, demand.START_H + 30)
    demand.add_event(values, start_h=demand.START_H,
                     timestamp_s=demand.START_H * 3600 + 5,
                     model="ChatGPT", log_type="Conversation log", request_tokens=0)
    demand.add_event(values, start_h=demand.START_H,
                     timestamp_s=(demand.START_H + 2) * 3600,
                     model="ChatGPT", log_type="Conversation log", request_tokens=17)
    item = values["ChatGPT"]
    assert item["count"][:3].tolist() == [1, 0, 1]
    assert item["volume"][:3].tolist() == [0, 0, 17]
    assert item["zero_token_requests"][:3].tolist() == [1, 0, 0]
    assert values["GPT-4"]["count"].sum() == 0


def test_features_and_live_baselines_ignore_unpublished_h_minus_one_and_future():
    volume = np.arange(1, 40, dtype=np.int64)
    count = np.ones(39, dtype=np.int64)
    h = demand.START_H + 25
    constants = {"train_fixed_p90": 100.0,
                 "last_available_residual_p90": 2.0,
                 "same_hour_yesterday_residual_p90": 3.0}
    feature = demand.feature_row(volume, count, h=h)
    baseline = demand.baseline_predictions(volume, np.array([h]), constants)
    changed_v, changed_n = volume.copy(), count.copy()
    changed_v[24:] = 999_999  # h-1 and all later hours
    changed_n[24:] = 999_999
    np.testing.assert_array_equal(feature, demand.feature_row(changed_v, changed_n, h=h))
    changed_base = demand.baseline_predictions(changed_v, np.array([h]), constants)
    for name in demand.BASELINES:
        np.testing.assert_array_equal(baseline[name], changed_base[name])
    changed_v[23] = 0  # h-2 is published at decision h
    assert not np.array_equal(feature, demand.feature_row(changed_v, changed_n, h=h))


def test_relative_hour_and_strict_fit_cutoff():
    train, validation = demand.split_hours()
    assert len(train) == 934 and len(validation) == 480
    assert train[0] == 225 * 24 + 25
    assert train[-1] == 265 * 24 - 2
    assert train[-1] + 2 == validation[0]
    assert 265 * 24 - 1 not in train  # this label is unavailable at val start
    assert validation[-1] == 285 * 24 - 1


def _row(timestamp: float, model: str, tokens: str, log: str = "Conversation log") -> str:
    return f"{timestamp},session,1,{model},{tokens},0,{tokens},{log}\n"


def test_reader_stops_before_parsing_test_target(tmp_path):
    file = tmp_path / "trace.csv"
    file.write_text(",".join(demand.HEADER) + "\n"
                    + _row(225 * 86400, "ChatGPT", "0")
                    + _row(265 * 86400, "GPT-4", "23")
                    + _row(285 * 86400, "ChatGPT", "NOT_AN_INTEGER"), encoding="utf-8")
    hourly, info = demand.load_preperiod(file, expected_pre_rows=2)
    assert info["stopped_before_test_target"] and info["preperiod_source_rows"] == 2
    assert hourly["ChatGPT"]["count"][0] == 1
    assert hourly["ChatGPT"]["volume"][0] == 0
    assert hourly["GPT-4"]["volume"][(265 - 225) * 24] == 23
    assert hourly["ChatGPT"]["count"][-1] == 0


def test_reader_rejects_unsorted_preperiod_and_missing_test_boundary(tmp_path):
    file = tmp_path / "trace.csv"
    file.write_text(",".join(demand.HEADER) + "\n"
                    + _row(225 * 86400 + 10, "ChatGPT", "2")
                    + _row(225 * 86400, "ChatGPT", "2")
                    + _row(285 * 86400, "ChatGPT", "1"), encoding="utf-8")
    with pytest.raises(ValueError, match="unsorted"):
        demand.load_preperiod(file, expected_pre_rows=2)
    file.write_text(",".join(demand.HEADER) + "\n"
                    + _row(225 * 86400, "ChatGPT", "2"), encoding="utf-8")
    with pytest.raises(ValueError, match="boundary"):
        demand.load_preperiod(file, expected_pre_rows=1)


def test_aggregation_deterministic_under_event_order():
    events = [(0, "ChatGPT", 0), (2, "GPT-4", 12),
              (0, "ChatGPT", 7), (2, "GPT-4", 8)]
    def aggregate(items):
        data = demand.empty_hourly(demand.START_H, demand.START_H + 4)
        for offset, model, tokens in items:
            demand.add_event(data, start_h=demand.START_H,
                             timestamp_s=(demand.START_H + offset) * 3600,
                             model=model, log_type="Conversation log", request_tokens=tokens)
        return data
    first, second = aggregate(events), aggregate(reversed(events))
    for model in demand.COHORTS:
        for field in ("count", "volume", "zero_token_requests"):
            np.testing.assert_array_equal(first[model][field], second[model][field])


def test_support_gate_uses_preperiod_only_and_all_hours(monkeypatch, tmp_path):
    frozen = tmp_path / "freeze.json"
    frozen.write_text("{}")
    monkeypatch.setattr(demand, "FREEZE", frozen)
    data = demand.empty_hourly(demand.START_H, demand.TEST_START_H)
    for model in demand.COHORTS:
        data[model]["count"][:] = 1
    summary = demand.preperiod_support(data, {"preperiod_source_rows": 10,
                                               "stopped_before_test_target": True})
    assert summary["all_pass"]
    assert summary["cohorts"]["ChatGPT"]["train"]["hours"] == 960
    data["GPT-4"]["count"][:960] = 0
    failed = demand.preperiod_support(data, {"preperiod_source_rows": 10,
                                              "stopped_before_test_target": True})
    assert not failed["all_pass"]


def test_fit_uses_only_mature_train_labels(monkeypatch):
    class RecordingRegressor:
        fitted = []
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def fit(self, x, y):
            self.fitted.append((x.copy(), y.copy()))
            return self
    monkeypatch.setattr(demand, "HistGradientBoostingRegressor", RecordingRegressor)
    data = demand.empty_hourly(demand.START_H, demand.TEST_START_H)
    for cohort in demand.COHORTS:
        volume = data[cohort]["volume"]
        volume[:(demand.VAL_START_H - demand.START_H - 1)] = 3
        volume[(demand.VAL_START_H - demand.START_H - 1):] = 1_000_000
        data[cohort]["count"][:] = 1
    train, _ = demand.split_hours()
    _, constants = demand.fit_train_only(data, train)
    assert len(RecordingRegressor.fitted) == 2
    assert all(len(y) == 934 and np.max(y) == 3 for _, y in RecordingRegressor.fitted)
    assert all(item["train_fixed_p90"] == 3 for item in constants.values())


def test_freeze_hash_and_version_guard(monkeypatch, tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("synthetic source")
    audit = tmp_path / "audit.json"
    audit.write_text("{}")
    protocol = tmp_path / "protocol.md"
    protocol.write_text("rule")
    script = tmp_path / "script.py"
    script.write_text("code")
    tests = tmp_path / "test.py"
    tests.write_text("assert True")
    for name, path in (("SOURCE", source), ("SOURCE_AUDIT", audit),
                       ("PROTOCOL", protocol), ("SCRIPT", script), ("TESTS", tests)):
        monkeypatch.setattr(demand, name, path)
    monkeypatch.setattr(demand, "SOURCE_BYTES", source.stat().st_size)
    monkeypatch.setattr(demand, "SOURCE_SHA256", demand.sha256_file(source))
    monkeypatch.setattr(demand, "FREEZE", tmp_path / "freeze_manifest.json")
    monkeypatch.setattr(demand, "SUPPORT", tmp_path / "support.json")
    monkeypatch.setattr(demand, "METRICS", tmp_path / "metrics.json")
    monkeypatch.setattr(demand, "MODELS", tmp_path / "models.joblib")
    demand.freeze()
    assert demand.verify_freeze()["status"] == "train_validation_only_no_test_targets"
    with pytest.raises(FileExistsError):
        demand.freeze()
    protocol.write_text("changed rule")
    with pytest.raises(ValueError, match="protocol_sha256"):
        demand.verify_freeze()
