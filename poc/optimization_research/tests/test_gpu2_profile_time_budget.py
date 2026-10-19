"""Data provenance and decision-time checks for the GPU2 profiling budget."""

import json

import pandas as pd
import pytest

from scripts import evaluate_gpu2_profile_time_budget as study


@pytest.fixture(scope="module")
def source_pair():
    snapshot = pd.read_parquet(study.SNAPSHOT)
    rows = snapshot[(snapshot.campaign == "khu-hf-local-exhaustive") &
                    snapshot.eligible & (snapshot.device == "cuda")]
    first = rows.iloc[0]
    group = rows
    for name in study.KEY:
        group = group[group[name] == first[name]]
    assert len(group) == 2
    return (group[group.replica_count == 1].iloc[0].to_dict(),
            group[group.replica_count == 2].iloc[0].to_dict())


def test_original_pairs_are_ordered_and_temporally_split():
    assert study.sha256(study.SNAPSHOT) == study.SNAPSHOT_SHA
    pairs = study.extract_pairs(pd.read_parquet(study.SNAPSHOT))
    train, test, abstain = study.split(pairs)
    assert (len(pairs), len(train), len(test), len(abstain)) == (2300, 1044, 1004, 252)
    assert (pd.to_datetime(pairs.gpu1_finished_at, utc=True) <
            pd.to_datetime(pairs.gpu2_started_at, utc=True)).all()
    assert (pairs.e1 > 0).all() and (pairs.e2 > 0).all()
    assert train.model_id.nunique() == test.model_id.nunique() == 8


def test_gpu2_post_result_cannot_change_inputs_or_target(source_pair):
    one, two = source_pair
    reference = study.pair_record(one, two)
    altered = dict(two)
    altered.update(result_json=json.dumps({"model_parameter_bytes": 999999999999}),
                   node_summary_json=json.dumps({"running_sample_count": 999}),
                   wall_seconds=999999, load_seconds=999999, compute_seconds=999999,
                   examples_per_second=999999)
    changed = study.pair_record(one, altered)
    assert changed == reference
    features = study.feature_matrix(pd.DataFrame([reference, changed]))
    pd.testing.assert_series_equal(features.iloc[0], features.iloc[1], check_names=False)


def test_pair_rejects_gpu1_result_available_too_late(source_pair):
    one, two = source_pair
    future_one = dict(one, finished_at=two["started_at"])
    with pytest.raises(ValueError, match="finish before"):
        study.pair_record(future_one, two)


def test_model_and_time_leakage_blocked_before_fit():
    pairs = study.extract_pairs(pd.read_parquet(study.SNAPSHOT))
    train, test, _ = study.split(pairs)
    model = sorted(test.model_id.unique())[0]
    target = test[test.model_id == model].iloc[:2]
    donor = train[train.model_id != model]
    with pytest.raises(ValueError, match="held-out model leaked"):
        study.predict_fold(pd.concat([donor, train[train.model_id == model].iloc[:1]]), target)
    future = donor.copy()
    future.loc[future.index[0], "gpu2_finished_at"] = "2026-09-11T14:00:00Z"
    with pytest.raises(ValueError, match="future donor"):
        study.predict_fold(future, target)


def test_feature_matrix_uses_only_gpu1_and_profile_fields():
    pairs = study.extract_pairs(pd.read_parquet(study.SNAPSHOT)).iloc[:3].copy()
    before = study.feature_matrix(pairs)
    pairs["e2"] *= 100
    pairs["gpu2_finished_at"] = "2099-01-01T00:00:00Z"
    pd.testing.assert_frame_equal(before, study.feature_matrix(pairs))
