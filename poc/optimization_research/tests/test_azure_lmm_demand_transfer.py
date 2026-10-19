import numpy as np
import pandas as pd

from evaluate_azure_lmm_demand_transfer import (
    aggregate_source,
    build_design,
    build_modality_features,
    MODALITY_FEATURES,
    N_SLOTS,
    START,
    TOKEN_FEATURES,
)


def test_aggregate_source_reads_only_allowed_columns(tmp_path):
    path = tmp_path / "mini.csv"
    pd.DataFrame({
        "TIMESTAMP": [
            "2024-10-15T12:00:10Z",
            "2024-10-15T12:04:59Z",
            "2024-10-15T12:05:00Z",
        ],
        "NumImages": [1, 0, 2],
        "ContextTokens": [100, 200, 300],
        "GeneratedTokens": [9999, 9999, 9999],
        "ignored_prompt": ["a", "b", "c"],
    }).to_csv(path, index=False)
    tokens, images, requests, coverage = aggregate_source(
        path, start=START, n_slots=3
    )
    assert tokens.tolist() == [300, 300, 0]
    assert images.tolist() == [1, 2, 0]
    assert requests.tolist() == [2, 1, 0]
    assert coverage["request_rows"] == 3
    assert coverage["empty_five_minute_bins"] == 1


def test_modality_features_use_only_prior_bins():
    images = np.arange(N_SLOTS, dtype=np.int64) % 5
    requests = np.arange(N_SLOTS, dtype=np.int64) % 7 + 1
    before = build_modality_features(images, requests)
    changed_images = images.copy()
    changed_requests = requests.copy()
    changed_images[300:] += 1000
    changed_requests[300:] += 2000
    after = build_modality_features(changed_images, changed_requests)
    columns = list(MODALITY_FEATURES)
    pd.testing.assert_series_equal(before.loc[300, columns],
                                   after.loc[300, columns])


def test_full_design_features_do_not_use_current_or_future_targets():
    tokens = np.full(N_SLOTS, 5000, dtype=np.int64)
    images = np.arange(N_SLOTS, dtype=np.int64) % 4
    requests = np.arange(N_SLOTS, dtype=np.int64) % 9 + 1
    first = build_design(tokens, images, requests)
    changed = tokens.copy()
    changed[400:] += 50_000
    second = build_design(changed, images, requests)
    feature_columns = list(TOKEN_FEATURES + MODALITY_FEATURES)
    pd.testing.assert_series_equal(first.loc[400, feature_columns],
                                   second.loc[400, feature_columns])
    assert first.loc[400, "y"] != second.loc[400, "y"]



def test_time_features_match_actual_utc_clock_and_weekend():
    tokens = np.full(N_SLOTS, 5000, dtype=np.int64)
    images = np.zeros(N_SLOTS, dtype=np.int64)
    requests = np.ones(N_SLOTS, dtype=np.int64)
    frame = build_design(tokens, images, requests)
    assert abs(frame.loc[0, "clock_sin"]) < 1e-12
    assert frame.loc[0, "clock_cos"] == -1.0
    assert frame.loc[0, "weekend"] == 0.0
    sunday_midnight = 4 * 288 + 144
    assert frame.loc[sunday_midnight, "weekend"] == 1.0
