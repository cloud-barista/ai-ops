"""Synthetic-only tests for the frozen-design Philly v3 validation scorer."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_philly_idle_rebound_adaptive_v3_validation as study
import philly_idle_rebound_adaptive_v3 as method


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for half, start in enumerate((study.HALVES[0][1], study.HALVES[1][1])):
        for i in range(4):
            y = i % 2
            row = {"machine_id": f"m{i % 2}", "decision_utc_s": start + i * 1800,
                   "utc_day": (start + i * 1800) // 86400,
                   "y": y, "candidate": 0.8 if y else 0.2}
            row.update({name: 0.5 for name in method.BASELINE_TIE_ORDER})
            row["frozen_train_beta"] = float(y) if half == 0 else 0.5
            row["expanding_fleet_40"] = 0.5 if half == 0 else float(y)
            rows.append(row)
    return pd.DataFrame(rows)


def test_scoring_uses_each_half_best_of_six_and_full_best_for_bootstrap():
    score = study.score_predictions(_toy_predictions(), draws=20)
    assert score["rows"] == 8 and score["positive"] == 4
    assert score["row_brier"]["candidate"] == pytest.approx(0.04)
    assert score["best_full_validation_simple_baseline"] == "frozen_train_beta"
    assert len(score["halves"]) == 2
    assert all(part["best_simple_brier"] == 0.0 for part in score["halves"])
    assert not score["acceptance"]["both_halves_no_worse_than_each_half_best_of_six"]
    assert score["best_machine_equal_simple_brier"] == min(
        score["machine_equal_brier"][name] for name in method.BASELINE_TIE_ORDER)
    assert set(score["paired_bootstrap"]) == {"machine_id", "utc_day"}
    assert all(row["draws"] == 20 and row["seed"] == 43
               for row in score["paired_bootstrap"].values())


def test_bootstrap_is_paired_deterministic_and_requires_two_clusters():
    frame = _toy_predictions()
    one = study.cluster_bootstrap(frame, "frozen_train_beta", "machine_id", draws=35)
    two = study.cluster_bootstrap(frame, "frozen_train_beta", "machine_id", draws=35)
    assert one == two and one["clusters"] == 2
    y = frame.y.to_numpy(float)
    delta = (frame.candidate.to_numpy(float) - y) ** 2 - (
        frame.frozen_train_beta.to_numpy(float) - y) ** 2
    assert one["point_delta"] == pytest.approx(float(np.mean(delta)))
    with pytest.raises(ValueError, match="two clusters"):
        study.cluster_bootstrap(frame[frame.machine_id.eq("m0")],
                                "frozen_train_beta", "machine_id", draws=5)


def test_low_risk_20pct_uses_fixed_hash_tie_and_same_row_count():
    frame = _toy_predictions()
    frame["candidate"] = 0.5
    chosen = study.low_risk_twenty_percent(frame, "candidate")
    ranked = sorted(zip(frame.machine_id, frame.decision_utc_s, frame.y),
                    key=lambda row: (method.low_risk_tie_key(row[0], row[1]),
                                     row[0], row[1]))
    assert chosen["rows"] == 2
    assert chosen["positive"] == sum(int(row[2]) for row in ranked[:2])


def test_nonfinite_duplicate_and_bad_label_predictions_fail_closed():
    frame = _toy_predictions()
    study.validate_predictions(frame)
    broken = frame.copy()
    broken.loc[0, "candidate"] = np.nan
    with pytest.raises(ValueError, match="Invalid probability"):
        study.validate_predictions(broken)
    broken = frame.copy()
    broken["y"] = broken.y.astype(float)
    broken.loc[0, "y"] = 0.5
    with pytest.raises(ValueError, match="binary"):
        study.validate_predictions(broken)
    broken = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate"):
        study.validate_predictions(broken)
    broken = frame.copy()
    broken["decision_utc_s"] = broken.decision_utc_s.astype(float)
    broken.loc[0, "decision_utc_s"] += 0.5
    with pytest.raises(ValueError, match="integers"):
        study.validate_predictions(broken)


def test_prediction_step_passes_only_input_chosen_anchors_not_candidate_labels(monkeypatch):
    t = study.HALVES[0][1]
    keys = [method.CandidateKey("m", t), method.CandidateKey("m", t + 1800)]
    anchor = method.AnchorOutcome("m", t, 0)
    seen = []
    def fake_probabilities(outcomes, target, decision, *, fleet_source_machine_ids):
        seen.append((list(outcomes), target, decision, fleet_source_machine_ids))
        return {"candidate": 0.1, **{name: 0.2 for name in method.BASELINE_TIE_ORDER}}
    monkeypatch.setattr(study.method, "probabilities", fake_probabilities)
    result = study.prediction_frame(keys, [anchor], {keys[0]: 0, keys[1]: 1},
                                    frozenset(("m",)))
    assert len(result) == 2 and result.y.tolist() == [0, 1]
    assert all(call[0] == [anchor] for call in seen)
    assert all(call[3] == {"m"} for call in seen)


def test_reader_filters_before_candidate_or_label_construction(monkeypatch):
    class ReaderReached(Exception):
        pass
    def fake_read(_, *, columns, filters):
        assert filters == [("time_utc_s", "<", study.support_audit.VALIDATION_END_S)]
        assert "machine_id" in columns and "gpu0_util" in columns
        raise ReaderReached
    monkeypatch.setattr(study.pd, "read_parquet", fake_read)
    with pytest.raises(ReaderReached):
        study.read_validation(Path("verified-source.parquet"))


def test_build_uses_input_anchors_then_requires_every_candidate_label(monkeypatch):
    start = study.HALVES[0][1]
    grid = np.arange(start - 2100, start - 2100 + 37 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    for i in (12, 18, 24, 30, 36):
        peak[i] = 20  # suppress later idle inputs without invalidating future labels
    peak[13] = 70  # both future labels are positive; neither anchor choice reads it
    monkeypatch.setattr(study.original, "windows_for_machine", lambda _: (grid, peak, valid))
    monkeypatch.setattr(study.support_audit, "verified_development_fleet_ids",
                        lambda ids, source: ids)
    monkeypatch.setattr(study, "EXPECTED_VALIDATION_N", 2)
    monkeypatch.setattr(study, "EXPECTED_VALIDATION_POSITIVE", 2)
    frame = pd.DataFrame({"machine_id": ["m"]})
    keys = [method.CandidateKey("m", start), method.CandidateKey("m", start + 1800)]
    anchors = [method.AnchorOutcome("m", start, 1)]
    expected = study.support_audit.summarize_support(keys, anchors, frozenset(("m",)))
    built_keys, built_anchors, labels, fleet_ids = study.build_validation(
        frame, {"machines": {"m": {}}}, expected)
    assert built_keys == keys and built_anchors == anchors
    assert labels == {keys[0]: 1, keys[1]: 1} and fleet_ids == {"m"}
    valid[36] = False  # nonanchor candidate future becomes unknown
    with pytest.raises(ValueError, match="incomplete future label"):
        study.build_validation(frame, {"machines": {"m": {}}}, expected)


def test_reviewed_authorization_sha_and_score_free_support_are_required(tmp_path, monkeypatch):
    payload = {
        "kind": "khu.philly_idle_rebound_adaptive_v3.validation_score_authorization",
        "status": "validation_only_pre_score_frozen",
        "score_free_freeze_sha256": study.EXPECTED_FREEZE_SHA,
        "score_free_support_sha256": study.EXPECTED_SUPPORT_SHA,
        "original_audit_sha256": study.EXPECTED_ORIGINAL_AUDIT_SHA,
        "development_source_sha256": study.EXPECTED_SOURCE_SHA,
        "scorer_sha256": study.original.sha256_file(study.SCRIPT),
        "test_sha256": study.original.sha256_file(study.TESTS),
        "scoring_note_sha256": study.original.sha256_file(study.NOTE),
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "pyarrow": pyarrow.__version__},
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    expected = study.original.sha256_file(path)
    auth, support = study.verify_authorization(expected, path)
    assert auth["status"] == "validation_only_pre_score_frozen"
    assert support["support_gate"]["all_pass"]
    with pytest.raises(ValueError, match="reviewed SHA"):
        study.verify_authorization("0" * 64, path)
    altered = tmp_path / "support.json"
    wrong_support = dict(support)
    wrong_support["support_gate"] = {"all_pass": False}
    altered.write_text(json.dumps(wrong_support), encoding="utf-8")
    monkeypatch.setattr(study.support_audit, "OUTPUT", altered)
    with pytest.raises(ValueError, match="score_free_support_sha256"):
        study.verify_authorization(expected, path)


def test_no_real_scoring_without_authorization_and_no_overwrite(monkeypatch, tmp_path):
    metrics = tmp_path / "metrics.json"
    predictions = tmp_path / "predictions.parquet"
    monkeypatch.setattr(study, "METRICS", metrics)
    monkeypatch.setattr(study, "PREDICTIONS", predictions)
    def forbidden(*_):
        raise AssertionError("Source must not be opened before authorization")
    monkeypatch.setattr(study.original, "verify_source", forbidden)
    with pytest.raises(ValueError, match="SHA256"):
        study.run("invalid")
    metrics.write_text("preexisting", encoding="utf-8")
    with pytest.raises(FileExistsError, match="overwrite"):
        study.run("invalid")
