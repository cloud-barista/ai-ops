"""Checks for repeated-run pairing and frozen out-of-campaign scoring."""

import json

import pandas as pd
import pytest

from scripts import evaluate_replica_cpu_work_external_campaigns as study


@pytest.fixture(scope="module")
def snapshot():
    return pd.read_parquet(study.original.SNAPSHOT)


def test_repeat_runs_are_paired_by_run_id_and_qwen_mismatch_excluded(snapshot):
    frame, audit = study.extract_external(snapshot, "khu-hf-live")
    assert audit == study.EXPECTED["khu-hf-live"]
    assert frame.loc[frame.workload_task == "inference", "model_id"].nunique() == 5
    assert not frame.duplicated(["run_id", *study.original.PAIR_KEY]).any()
    assert audit["input_encoding_mismatch_qwen"] == 1


def test_diverse_cpu_campaign_has_real_cpu_core_variation(snapshot):
    frame, audit = study.extract_external(snapshot, "khu-hf-diverse-cpu-matrix")
    assert audit == study.EXPECTED["khu-hf-diverse-cpu-matrix"]
    encoder = frame.loc[frame.workload_task == "inference"]
    assert encoder.groupby("model_id").cpu_core_limit.nunique().eq(4).all()


def test_external_score_requires_matching_freeze(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(study, "FREEZE", missing)
    with pytest.raises(ValueError, match="Freeze missing"):
        study.evaluate()
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"script_sha256": "0" * 64}))
    monkeypatch.setattr(study, "FREEZE", wrong)
    with pytest.raises(ValueError, match="differs from freeze"):
        study.evaluate()
