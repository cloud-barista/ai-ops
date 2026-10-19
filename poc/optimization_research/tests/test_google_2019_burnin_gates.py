"""Causality and zero-error tie checks for exploratory Borg expert gates."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_google_2019_burnin_gates.py"
spec = importlib.util.spec_from_file_location("google_2019_burnin_gates", SCRIPT)
study = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(study)


def test_current_and_future_labels_cannot_change_current_choice_or_forecast() -> None:
    step = np.arange(2300)
    counts = (100 + (step % 288) // 5 + (step // 288) * 2).astype(float)
    target = 1000
    offsets, actual, predictions = study.forecasts(counts)
    fixed = study.select_fixed(offsets, actual, predictions, study.FIRST_CUTOFF)
    two, _ = study.rolling_choices(offsets, actual, predictions, False)
    three, _ = study.rolling_choices(offsets, actual, predictions, True)

    modified = counts.copy()
    modified[target:] = 100_000 + (step[target:] % 17)
    next_offsets, next_actual, next_predictions = study.forecasts(modified)
    next_fixed = study.select_fixed(next_offsets, next_actual, next_predictions,
                                    study.FIRST_CUTOFF)
    next_two, _ = study.rolling_choices(next_offsets, next_actual, next_predictions, False)
    next_three, _ = study.rolling_choices(next_offsets, next_actual, next_predictions, True)
    position = target - study.START
    assert offsets[position] == next_offsets[position] == target
    assert actual[position] != next_actual[position]
    assert all(predictions[name][position] == next_predictions[name][position]
               for name in study.EXPERTS)
    assert fixed == next_fixed
    assert two[target - study.FIRST_CUTOFF] == next_two[target - study.FIRST_CUTOFF]
    assert three[target - study.FIRST_CUTOFF] == next_three[target - study.FIRST_CUTOFF]


def test_zero_error_tie_stays_with_conservative_mean12() -> None:
    offsets = np.arange(study.START, 900)
    actual = np.zeros(len(offsets))
    predictions = {name: np.zeros(len(offsets)) for name in study.EXPERTS}
    fixed = study.select_fixed(offsets, actual, predictions, study.FIRST_CUTOFF)
    two, two_switches = study.rolling_choices(offsets, actual, predictions, False)
    three, three_switches = study.rolling_choices(offsets, actual, predictions, True)
    assert fixed["chosen"] == "mean12"
    assert two == three == ["mean12"] * (900 - study.FIRST_CUTOFF)
    assert two_switches == three_switches == []
