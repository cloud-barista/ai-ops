"""Synthetic causality and boundary tests; no real test-period outcomes."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_burstgpt_output_demand as demand


def test_aggregation_keeps_zero_response_and_unlogged_hour_distinct():
    data = demand.empty_hourly(demand.START_H, demand.START_H + 4)
    demand.add_event(data, start_h=demand.START_H,
                     timestamp_s=demand.START_H * 3600 + 1, model="ChatGPT",
                     log_type="Conversation log", request_tokens=5, response_tokens=0)
    demand.add_event(data, start_h=demand.START_H,
                     timestamp_s=(demand.START_H + 2) * 3600 + 1, model="ChatGPT",
                     log_type="Conversation log", request_tokens=0, response_tokens=7)
    demand.add_event(data, start_h=demand.START_H,
                     timestamp_s=(demand.START_H + 2) * 3600 + 2, model="GPT-4",
                     log_type="API log", request_tokens=99, response_tokens=99)
    x = data["ChatGPT"]
    assert x["count"].tolist() == [1, 0, 1, 0]
    assert x["input"].tolist() == [5, 0, 0, 0]
    assert x["output"].tolist() == [0, 0, 7, 0]
    assert x["zero_output_requests"].tolist() == [1, 0, 0, 0]
    assert data["GPT-4"]["count"].sum() == 0


def test_features_and_baselines_use_only_published_hours():
    output = np.arange(1, 50, dtype=np.int64)
    input_ = np.arange(51, 100, dtype=np.int64)
    count = np.ones(49, dtype=np.int64)
    h = demand.START_H + 25
    constants = {"train_fixed_p90": 90.0,
                 "last_available_residual_p90": 2.0,
                 "same_hour_yesterday_residual_p90": 3.0}
    original = demand.feature_row(output, input_, count, h=h)
    baseline = demand.baseline_predictions(output, np.array([h]), constants)
    changed = [x.copy() for x in (output, input_, count)]
    for x in changed:
        x[24:] = 100_000  # h-1, h and later must be invisible
    np.testing.assert_array_equal(original, demand.feature_row(*changed, h=h))
    changed_baseline = demand.baseline_predictions(changed[0], np.array([h]), constants)
    for name in demand.BASELINES:
        np.testing.assert_array_equal(baseline[name], changed_baseline[name])
    changed[0][23] = 0  # h-2 is available and must affect a feature
    assert not np.array_equal(original, demand.feature_row(*changed, h=h))


def test_fit_excludes_unpublished_final_train_hour(monkeypatch):
    class RecordingRegressor:
        fitted = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def fit(self, x, y):
            self.fitted.append(y.copy())
            return self

    monkeypatch.setattr(demand, "HistGradientBoostingRegressor", RecordingRegressor)
    hourly = demand.empty_hourly(demand.START_H, demand.TEST_START_H)
    for cohort in demand.COHORTS:
        output = hourly[cohort]["output"]
        output[:] = 3
        output[demand.VAL_START_H - demand.START_H - 1:] = 1_000_000
    train, validation = demand.split_hours()
    _, constants = demand.fit_train_only(hourly, train)
    assert train[-1] + 2 == validation[0]
    assert len(train) == 934 and len(validation) == 480
    assert all(len(y) == 934 and np.max(y) == 3 for y in RecordingRegressor.fitted)
    assert all(item["train_fixed_p90"] == 3 for item in constants.values())


def _row(timestamp: int, model: str, input_tokens: str, output_tokens: str,
         log_type: str = "Conversation log") -> str:
    return (f"{timestamp},session,1,{model},{input_tokens},{output_tokens},"
            f"0,{log_type}\n")


def test_reader_stops_before_parsing_test_output(tmp_path):
    source = tmp_path / "trace.csv"
    source.write_text(
        ",".join(demand.HEADER) + "\n"
        + _row(225 * 86400, "ChatGPT", "5", "0")
        + _row(265 * 86400, "GPT-4", "23", "77")
        + _row(285 * 86400, "ChatGPT", "5", "NOT_AN_INTEGER"),
        encoding="utf-8")
    hourly, info = demand.load_preperiod(source, expected_pre_rows=2)
    assert info == {"preperiod_source_rows": 2, "stopped_before_test_target": True}
    assert hourly["ChatGPT"]["zero_output_requests"][0] == 1
    assert hourly["GPT-4"]["output"][(265 - 225) * 24] == 77


def test_support_failure_does_not_parse_non_numeric_output(monkeypatch, tmp_path):
    source = tmp_path / "trace.csv"
    source.write_text(
        ",".join(demand.HEADER) + "\n"
        + _row(225 * 86400, "ChatGPT", "5", "NOT_AN_INTEGER")
        + _row(285 * 86400, "GPT-4", "5", "ALSO_NOT_AN_INTEGER"),
        encoding="utf-8")
    hourly, info = demand.load_preperiod_support(source, expected_pre_rows=1)
    assert info["stopped_before_test_target"]
    assert hourly["ChatGPT"]["count"][0] == 1
    frozen = tmp_path / "freeze.json"
    frozen.write_text("{}")
    monkeypatch.setattr(demand, "FREEZE", frozen)
    for name, leaf in (("SUPPORT", "support.json"), ("MODELS", "models.joblib"),
                       ("PREDICTIONS", "pred.csv"), ("METRICS", "metrics.json")):
        monkeypatch.setattr(demand, name, tmp_path / leaf)
    monkeypatch.setattr(demand, "verify_freeze", lambda: {})
    monkeypatch.setattr(demand, "load_preperiod_support", lambda: (hourly, info))

    def fail_if_outcomes_read():
        raise AssertionError("The target pass must not run after a support failure")

    monkeypatch.setattr(demand, "load_preperiod", fail_if_outcomes_read)
    with pytest.raises(ValueError, match="support failed"):
        demand.select()
    assert (tmp_path / "support.json").is_file()
    assert not (tmp_path / "metrics.json").exists()


def test_reader_rejects_unsorted_or_unclosed_preperiod(tmp_path):
    source = tmp_path / "trace.csv"
    source.write_text(
        ",".join(demand.HEADER) + "\n"
        + _row(225 * 86400 + 10, "ChatGPT", "5", "1")
        + _row(225 * 86400, "ChatGPT", "5", "1")
        + _row(285 * 86400, "ChatGPT", "5", "1"), encoding="utf-8")
    with pytest.raises(ValueError, match="unsorted"):
        demand.load_preperiod(source, expected_pre_rows=2)
    source.write_text(",".join(demand.HEADER) + "\n"
                      + _row(225 * 86400, "ChatGPT", "5", "1"), encoding="utf-8")
    with pytest.raises(ValueError, match="boundary"):
        demand.load_preperiod(source, expected_pre_rows=1)


def test_support_gate_uses_logged_requests_not_positive_output(monkeypatch, tmp_path):
    frozen = tmp_path / "freeze.json"
    frozen.write_text("{}")
    monkeypatch.setattr(demand, "FREEZE", frozen)
    hourly = demand.empty_hourly(demand.START_H, demand.TEST_START_H)
    for cohort in demand.COHORTS:
        hourly[cohort]["count"][:] = 1
    passed = demand.preperiod_support(hourly, {"preperiod_source_rows": 2,
                                                "stopped_before_test_target": True})
    assert passed["all_pass"]
    assert passed["cohorts"]["ChatGPT"]["train"]["hours"] == 960
    hourly["GPT-4"]["count"][:960] = 0
    failed = demand.preperiod_support(hourly, {"preperiod_source_rows": 2,
                                                "stopped_before_test_target": True})
    assert not failed["all_pass"]


def test_freeze_detects_protocol_change_and_refuses_overwrite(monkeypatch, tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("synthetic")
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
    for name, leaf in (("FREEZE", "freeze.json"), ("SUPPORT", "support.json"),
                       ("MODELS", "models.joblib"), ("PREDICTIONS", "pred.csv"),
                       ("METRICS", "metrics.json")):
        monkeypatch.setattr(demand, name, tmp_path / leaf)
    demand.freeze()
    assert demand.verify_freeze()["status"] == "train_validation_only_no_test_targets_exploratory"
    with pytest.raises(FileExistsError):
        demand.freeze()
    protocol.write_text("changed rule")
    with pytest.raises(ValueError, match="protocol_sha256"):
        demand.verify_freeze()
