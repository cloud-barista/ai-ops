"""Focused checks for the frozen Google 2011 observed-resource experiment."""

from __future__ import annotations

import importlib.util
import csv
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_google_2011_observed_resource.py"
SPEC = importlib.util.spec_from_file_location("google_observed_resource", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def test_coverage_unions_overlapping_intervals() -> None:
    assert study.interval_coverage([(0, 4), (2, 7), (9, 10)]) == 8


def test_job_weights_and_weighted_quantile_prevent_large_job_dominance() -> None:
    frame = pd.DataFrame({"job": ["large"] * 100 + ["small"]})
    weights = study.job_weights(frame)
    assert np.isclose(weights[:100].sum(), 1)
    assert weights[-1] == 1
    values = np.array([1.0] * 100 + [10.0])
    assert study.weighted_quantile(values, weights, 0.90) == 10.0


def test_pinball_and_job_equal_metric_differ_from_row_metric() -> None:
    frame = pd.DataFrame({"job": ["a", "a", "b"], "max_cpu": [0.0, 0.0, 10.0]})
    prediction = {"request": np.zeros(3)}
    table = study.job_table(frame, "cpu", prediction)
    assert np.isclose(table.request_loss.mean(), 4.5)
    assert np.isclose(np.average(table.request_loss, weights=table.n), 3.0)
    assert np.isclose(study.pinball(np.array([10.0]), np.array([0.0]))[0], 9.0)


def test_validation_selection_uses_job_equal_pinball_and_stable_tie_break() -> None:
    scores = {target: {method: {"job_equal_pinball": 2.0}
                       for method in study.METHODS} for target in study.TARGETS}
    scores["cpu"]["hgb"]["job_equal_pinball"] = 1.0
    scores["memory"]["request"]["job_equal_pinball"] = 1.0
    scores["memory"]["global_ratio"]["job_equal_pinball"] = 1.0
    assert study.select(scores) == {"cpu": "hgb", "memory": "request"}


def test_scoring_uses_shared_job_counts_for_each_candidate(monkeypatch) -> None:
    frame = pd.DataFrame({"job": ["a", "a", "b"], "max_cpu": [0.0, 0.0, 10.0],
                          "max_memory": [0.0, 0.0, 10.0]})
    monkeypatch.setattr(study, "predict", lambda _frame, _bundle, _target:
                        {method: np.zeros(3) for method in study.METHODS})
    metrics, _ = study.scores(frame, {})
    assert metrics["cpu"]["request"]["jobs"] == 2
    assert metrics["cpu"]["request"]["rows"] == 3
    assert np.isclose(metrics["cpu"]["request"]["job_equal_pinball"], 4.5)
    assert np.isclose(metrics["memory"]["hgb"]["row_pinball"], 3.0)


def test_test_stage_rejects_changed_protocol_before_reading_labels(tmp_path, monkeypatch) -> None:
    (tmp_path / "freeze_manifest.json").write_text(json.dumps({"protocol_sha256": "wrong"}))
    monkeypatch.setattr(study, "OUT", tmp_path)

    def forbidden(_shard):
        raise AssertionError("Held-out labels must not be opened")

    monkeypatch.setattr(study, "extract_shard", forbidden)
    with pytest.raises(ValueError, match="Protocol changed"):
        study.test()


def test_extraction_uses_aggregation_column_18_not_disk_column_12(tmp_path, monkeypatch) -> None:
    for kind in ("task_events", "task_usage"):
        (tmp_path / kind).mkdir()
    events = []
    for task in ("clean", "aggregated"):
        submit = ["0", "", "job", task, "", "0", "user", "1", "9", "0.1", "0.1", "0", "0"]
        schedule = submit.copy()
        schedule[4], schedule[5] = "machine", "1"
        events.extend((submit, schedule))
    marker = [str(study.SPAN + 1), "", "marker", "0", "", "2", "", "", "", "", "", "", ""]
    events.append(marker)
    usage = []
    for task in ("clean", "aggregated"):
        for index in range(5):
            row = [""] * 20
            row[0] = str(index * study.SPAN // 5)
            row[1] = str((index + 1) * study.SPAN // 5)
            row[2:5] = ["job", task, "machine"]
            row[10], row[13] = "0.05", "0.05"
            row[12] = "1" if task == "clean" else "0"  # disk, not aggregation
            row[18] = "1" if task == "aggregated" and index == 0 else "0"
            usage.append(row)
    for kind, rows in (("task_events", events), ("task_usage", usage)):
        with gzip.open(tmp_path / kind / "part-00050-of-00500.csv.gz", "wt", newline="") as handle:
            csv.writer(handle).writerows(rows)
    monkeypatch.setattr(study, "SOURCE", tmp_path)
    frame, info = study.extract_shard(50)
    assert info["valid_and_covered"] == 2
    assert info["aggregation_peak_excluded"] == 1
    assert frame.task.tolist() == ["clean"]
