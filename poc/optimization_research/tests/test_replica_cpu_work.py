"""Meaningful source, timing, leakage, and freeze checks for CPU-work study."""

import json

import numpy as np
import pandas as pd
import pytest

from scripts import evaluate_replica_cpu_work as study


@pytest.fixture(scope="module")
def original_rows():
    snapshot = pd.read_parquet(study.SNAPSHOT)
    frame = snapshot.loc[(snapshot.campaign == study.MAIN) & snapshot.eligible &
                         (snapshot.success == 1) & (snapshot.device == "cuda") &
                         (snapshot.replica_count.isin([1, 2]))]
    first = frame.iloc[0]
    key = tuple(first[name] for name in study.PAIR_KEY)
    group = frame.loc[frame.apply(lambda row: tuple(row[name] for name in study.PAIR_KEY) == key,
                                  axis=1)]
    assert len(group) == 2
    return (group.loc[group.replica_count == 1].iloc[0].to_dict(),
            group.loc[group.replica_count == 2].iloc[0].to_dict())


def test_original_pairs_have_audited_shape_and_distinct_target():
    pairs = study.load_pairs()
    main = pairs[study.MAIN]
    assert len(main) == 2300
    assert len(main.loc[main.workload_task == "inference"]) == 2048
    assert len(main.loc[main.workload_task == "causal_lm_forward"]) == 252
    assert (main.gpu2_total_cpu_seconds > 2 * main.gpu1_cpu_seconds).all()


def test_pair_rejects_child_cpu_mismatch(original_rows):
    one, two = original_rows
    changed = dict(two)
    result = json.loads(two["result_json"])
    result["replica_results"][0]["cpu_seconds"] += 1
    changed["result_json"] = json.dumps(result)
    with pytest.raises(ValueError, match="GPU2 child sum"):
        study.pair_to_record(one, changed)


def test_pair_rejects_future_gpu1_observation(original_rows):
    one, two = original_rows
    changed = dict(one)
    changed["finished_at"] = two["started_at"]
    with pytest.raises(ValueError, match="must finish before"):
        study.pair_to_record(changed, two)


def test_features_do_not_use_gpu2_labels():
    pairs = study.load_pairs()[study.MAIN].iloc[:3].copy()
    baseline = study.feature_matrix(pairs)
    pairs["gpu2_total_cpu_seconds"] *= 100
    pairs["gpu2_max_child_cpu_seconds"] *= 100
    pd.testing.assert_frame_equal(baseline, study.feature_matrix(pairs))


def test_frozen_evaluation_refuses_changed_code_hash(monkeypatch, tmp_path):
    content = json.loads(study.FREEZE.read_text())
    content["script_sha256"] = "0" * 64
    invalid = tmp_path / "freeze.json"
    invalid.write_text(json.dumps(content))
    monkeypatch.setattr(study, "FREEZE", invalid)
    with pytest.raises(ValueError, match="differs from freeze"):
        study.evaluate()


def test_macro_score_uses_equal_models():
    rows = []
    for model_id, sizes, error in (("small", 2, 1.0), ("large", 20, 2.0)):
        for index in range(sizes):
            row = {name: "x" for name in study.PAIR_KEY}
            row.update(model_id=model_id, workload_group=f"{model_id}_{index}",
                       gpu2_total_cpu_seconds=1.0)
            for method in study.METHODS:
                row[f"pred_{method}_cpu_seconds"] = float(np.exp(error))
            rows.append(row)
    scores = study.score_campaign(pd.DataFrame(rows))
    assert scores["macro"]["hgb_ratio"]["mean_abs_log_error"] == pytest.approx(1.5)
