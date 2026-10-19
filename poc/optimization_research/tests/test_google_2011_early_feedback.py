"""Temporal boundary and gate checks for the Google 2011 feedback study."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_google_2011_early_feedback.py"
SPEC = importlib.util.spec_from_file_location("google_2011_early_feedback", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def event(time: int, kind: str, machine: str = "machine") -> study.Event:
    return study.Event(time, kind, machine, "", "0.1", "0.2", "1", "7")


def usage(start: int, end: int, cpu: str = "0.1", memory: str = "0.2",
          machine: str = "machine", aggregation: str = "0") -> list[str]:
    row = [""] * 20
    row[0], row[1], row[4] = str(start), str(end), machine
    row[10], row[13], row[18] = memory, cpu, aggregation
    return row


def test_lifecycle_requires_single_schedule_unchanged_request_and_survival() -> None:
    initial = [event(0, "0", ""), event(study.US, "1")]
    assert study.valid_episode(initial, study.HORIZON + study.US) is not None
    assert study.valid_episode(initial + [event(600 * study.US, "2")],
                               study.HORIZON + study.US) is None
    assert study.valid_episode(initial + [event(2 * study.US, "1")],
                               study.HORIZON + study.US) is None
    changed = study.Event(study.US, "1", "machine", "", "0.3", "0.2", "1", "7")
    assert study.valid_episode([initial[0], changed],
                               study.HORIZON + study.US) is None


def test_usage_rejects_other_machine_aggregation_and_invalid_measurements() -> None:
    episode = study.Episode(0, study.HORIZON, "machine", 0.1, 0.2, 1, 7)
    assert not study.append_valid_usage(episode, usage(0, 300 * study.US, machine="other"))
    assert not study.append_valid_usage(episode, usage(0, 300 * study.US, aggregation="1"))
    assert not study.append_valid_usage(episode, usage(0, 300 * study.US, cpu="nan"))
    assert not study.append_valid_usage(episode, usage(0, 300 * study.US, cpu="-0.1"))
    assert study.append_valid_usage(episode, usage(0, 300 * study.US))
    assert len(episode.usage) == 1


def test_decision_uses_only_completed_prefix_and_strictly_later_target() -> None:
    episode = study.Episode(0, study.HORIZON, "machine", 0.1, 0.2, 1, 7)
    episode.usage = [(-60 * study.US, 300 * study.US, 100.0, 200.0),
                     (0, 300 * study.US, 1.0, 2.0),
                     (200 * study.US, 400 * study.US, 99.0, 99.0),
                     (300 * study.US, 600 * study.US, 3.0, 4.0),
                     (600 * study.US, 1800 * study.US, 5.0, 6.0)]
    row = study.episode_row("job", "task", episode)
    assert row is not None
    assert row["decision_offset_s"] == 300
    assert row["pre_cpu_peak"] == 1.0
    assert row["later_cpu_peak"] == 5.0
    assert row["pre_schedule_report_excluded"] == 1
    assert row["pre_coverage"] == 1.0
    assert row["later_coverage"] == 1.0


def test_decision_requires_report_end_between_five_and_ten_minutes() -> None:
    episode = study.Episode(0, study.HORIZON, "machine", 0.1, 0.2, 1, 7)
    episode.usage = [(0, 299 * study.US, 1, 1),
                     (601 * study.US, 1800 * study.US, 2, 2)]
    assert study.episode_row("job", "task", episode) is None


def test_features_cannot_read_future_target_or_coverage() -> None:
    frame = pd.DataFrame(dict(job=["a"], task=["x"], cpu_request=[0.1],
                              memory_request=[0.2], scheduling_class=[1], priority=[7],
                              pre_cpu_peak=[0.5], pre_memory_peak=[0.25], pre_rows=[2],
                              decision_offset_s=[350], pre_coverage=[0.9],
                              later_cpu_peak=[1.0], later_memory_peak=[2.0],
                              later_coverage=[0.8]))
    before = study.features(frame, True)
    frame.loc[0, "later_cpu_peak"] = 999.0
    frame.loc[0, "later_memory_peak"] = 999.0
    frame.loc[0, "later_coverage"] = 0.01
    np.testing.assert_array_equal(study.features(frame, True), before)
    assert study.features(frame, False).shape[1] == 4


def test_job_weights_and_calibration_jobs_are_disjoint() -> None:
    frame = pd.DataFrame({"job": ["a"] * 100 + ["b", "c", "d", "e"]})
    weights = study.job_weights(frame)
    assert weights[:100].sum() == pytest.approx(1.0)
    assert set(weights[100:]) == {1.0}
    fit, calibration = study.split_fit_calibration(frame)
    assert not (set(fit.job) & set(calibration.job))
    assert set(fit.job) | set(calibration.job) == set(frame.job)


def test_gate_requires_every_robustness_condition() -> None:
    metrics = {method: dict(composite=1.0, cpu_pinball=1.0,
                            memory_pinball=1.0, joint_coverage=0.9)
               for method in study.METHODS}
    metrics["feedback_hgb"] = dict(composite=0.8, cpu_pinball=0.8,
                                    memory_pinball=0.8, joint_coverage=0.86)
    jobs = pd.DataFrame({"count": [5] * 5,
                         "request_ratio_composite": [1.0] * 5,
                         "feedback_hgb_composite": [0.8] * 5},
                        index=["a", "b", "c", "d", "e"])
    axis_reference = {"cpu": "request_ratio", "memory": "request_ratio"}
    approved = study.gate(metrics, jobs, "request_ratio", 40, 15, 30, 1000, axis_reference)
    assert approved["passed"]
    assert not study.gate(metrics, jobs, "request_ratio", 39, 15, 30, 1000,
                          axis_reference)["passed"]
    metrics["feedback_hgb"]["joint_coverage"] = 0.84
    assert not study.gate(metrics, jobs, "request_ratio", 40, 15, 30, 1000,
                          axis_reference)["passed"]


def test_failed_validation_cannot_fetch_or_score_test(tmp_path, monkeypatch) -> None:
    (tmp_path / "validation.json").write_text(json.dumps({"decision": {"passed": False}}))
    monkeypatch.setattr(study, "OUT", tmp_path)
    monkeypatch.setattr(study, "check_freeze", lambda: {"expected_test_sha256": {}})
    monkeypatch.setattr(study.urllib.request, "urlopen",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("test network must stay closed")))
    monkeypatch.setattr(study, "extract_shard",
                        lambda _shard: (_ for _ in ()).throw(
                            AssertionError("test labels must stay closed")))
    with pytest.raises(ValueError, match="Validation gate failed"):
        study.fetch_test()
    with pytest.raises(ValueError, match="Validation gate failed"):
        study.test()
