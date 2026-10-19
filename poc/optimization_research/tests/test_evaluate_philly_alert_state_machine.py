from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_philly_alert_state_machine import (  # noqa: E402
    eligible_event_starts,
    exploratory_gate,
    issue_indices,
    summarize,
)


def group(times, scores, high, y):
    return {"machine": "test-machine", "slot": 0,
            "times": np.asarray(times, dtype=np.int64),
            "scores": np.asarray(scores, dtype=float),
            "high": np.asarray(high, dtype=bool),
            "y": np.asarray(y, dtype=np.int8)}


def test_confirmations_and_cooldown_use_only_scores_and_current_state():
    times = np.arange(10, dtype=np.int64) * 300
    scores = np.asarray([0.6, 0.7, 0.8, 0.1, 0.8, 0.8, 0.9, 0.9, 0.9, 0.9])
    high = np.zeros(10, dtype=bool)
    assert issue_indices(times, scores, high, 0.5, 2, cooldown_s=900) == [1, 5, 8]
    high[5] = True
    assert issue_indices(times, scores, high, 0.5, 2, cooldown_s=900) == [1, 7]


def test_gap_resets_confirmation_and_first_high_is_left_censored():
    times = np.asarray([0, 300, 900, 1200, 1500], dtype=np.int64)
    scores = np.ones(5)
    high = np.asarray([True, False, False, False, True])
    assert issue_indices(times, scores, high, 0.5, 2) == []
    assert issue_indices(times, scores, high, 0.5, 1, cooldown_s=0) == [1, 3]
    assert eligible_event_starts(times, high) == []


def test_event_lead_uses_actual_window_start_not_decision_timestamp():
    times = np.arange(11, dtype=np.int64) * 300
    high = np.zeros(11, dtype=bool)
    high[9:11] = True
    # The high window at decision 2700 started at 2100. Alerts at 1800 lead it.
    assert eligible_event_starts(times, high) == [2100]
    scores = np.zeros(11)
    scores[6] = 0.8
    result = summarize([group(times, scores, high, np.zeros(11))], 0.5, 1)
    assert result["alerts"] == 1
    assert result["detected_high_starts"] == 1
    assert result["median_lead_minutes"] == 5


def test_outcomes_change_precision_but_cannot_change_alert_times():
    times = np.arange(8, dtype=np.int64) * 300
    scores = np.asarray([0.6, 0.6, 0.1, 0.1, 0.7, 0.7, 0.1, 0.1])
    high = np.zeros(8, dtype=bool)
    a = summarize([group(times, scores, high, np.zeros(8))], 0.5, 2)
    b = summarize([group(times, scores, high, np.ones(8))], 0.5, 2)
    assert a["alerts"] == b["alerts"]
    assert a["alert_precision"] == 0
    assert b["alert_precision"] == 1


def test_gate_abstains_when_no_alert_or_high_event_can_be_scored():
    baseline = {"alert_precision": 0.5, "event_recall": None}
    candidate = {"alert_precision": 0.6, "event_recall": None}
    assert exploratory_gate(baseline, candidate, 0) is False
    assert exploratory_gate(baseline, candidate, None) is False
