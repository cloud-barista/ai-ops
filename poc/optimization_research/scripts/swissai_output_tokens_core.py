"""Causal, day-start summaries for SwissAI output-token research.

The caller supplies only outcome rows whose labels are eligible for the
conditional research target. This module has no access to the raw trace.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


DAY_US = 86_400_000_000
Q = 0.9
SHRINK_COUNT = 100


@dataclass(frozen=True)
class WindowSummary:
    count: int
    p90: float
    model_count: np.ndarray
    model_p50: np.ndarray
    model_p90: np.ndarray


@dataclass(frozen=True)
class DaySnapshot:
    one_day: WindowSummary
    seven_day: WindowSummary


def higher_quantile(values: np.ndarray, quantile: float) -> float:
    if not len(values):
        return float('nan')
    return float(np.quantile(values, quantile, method='higher'))


def _summarize_window(codes: np.ndarray, values: np.ndarray,
                      model_count: int) -> WindowSummary:
    counts = np.zeros(model_count, dtype=np.int64)
    p50 = np.full(model_count, np.nan, dtype=np.float64)
    p90 = np.full(model_count, np.nan, dtype=np.float64)
    if len(values):
        order = np.argsort(codes, kind='stable')
        sorted_codes = codes[order]
        sorted_values = values[order]
        unique, starts, lengths = np.unique(
            sorted_codes, return_index=True, return_counts=True)
        for code, start, length in zip(unique, starts, lengths):
            if not 0 <= int(code) < model_count:
                raise ValueError('model_code_out_of_range')
            subset = sorted_values[start:start + length]
            counts[code] = length
            p50[code] = higher_quantile(subset, 0.5)
            p90[code] = higher_quantile(subset, Q)
    return WindowSummary(len(values), higher_quantile(values, Q), counts,
                         p50, p90)


def build_day_snapshots(day_numbers: np.ndarray, available_us: np.ndarray,
                        model_codes: np.ndarray, output_tokens: np.ndarray,
                        model_count: int) -> dict[int, DaySnapshot]:
    """Use only labels with ``available_us < UTC day start``.

    Available time is the recorded finish plus the declared feedback delay.
    The seven-day window is based on *availability*, not request arrival.
    Requests on the same UTC day share one snapshot.
    """
    day_numbers = np.asarray(day_numbers, dtype=np.int64)
    available_us = np.asarray(available_us, dtype=np.int64)
    model_codes = np.asarray(model_codes, dtype=np.int32)
    output_tokens = np.asarray(output_tokens, dtype=np.int64)
    if not (len(available_us) == len(model_codes) == len(output_tokens)):
        raise ValueError('event_arrays_differ_in_length')
    if model_count < 1 or np.any(output_tokens < 0):
        raise ValueError('invalid_history_population')
    order = np.argsort(available_us, kind='stable')
    available = available_us[order]
    codes = model_codes[order]
    outputs = output_tokens[order]
    result: dict[int, DaySnapshot] = {}
    for day in sorted(set(day_numbers.tolist())):
        start = int(day) * DAY_US
        end = int(np.searchsorted(available, start, side='left'))
        lower_one = int(np.searchsorted(available, start - DAY_US, side='left'))
        lower_seven = int(np.searchsorted(available, start - 7 * DAY_US,
                                          side='left'))
        one = _summarize_window(codes[lower_one:end],
                                outputs[lower_one:end], model_count)
        seven = _summarize_window(codes[lower_seven:end],
                                  outputs[lower_seven:end], model_count)
        result[day] = DaySnapshot(one, seven)
    return result


def static_quantiles(model_codes: np.ndarray, output_tokens: np.ndarray,
                     model_count: int) -> tuple[float, np.ndarray]:
    """Return global p90 and model p90 shrunk toward it by 100 requests."""
    if not len(output_tokens):
        raise ValueError('empty_static_training_population')
    summary = _summarize_window(np.asarray(model_codes, dtype=np.int32),
                                np.asarray(output_tokens, dtype=np.int64),
                                model_count)
    global_p90 = summary.p90
    weights = summary.model_count / (summary.model_count + SHRINK_COUNT)
    model_p90 = np.where(summary.model_count > 0,
                         weights * summary.model_p90 +
                         (1.0 - weights) * global_p90,
                         global_p90)
    return global_p90, model_p90


def recent_prediction(snapshot: DaySnapshot, model_code: np.ndarray,
                      static_global_p90: float) -> np.ndarray:
    """Seven-day model p90 with a recent-global backoff."""
    codes = np.asarray(model_code, dtype=np.int32)
    seven = snapshot.seven_day
    global_p90 = (seven.p90 if seven.count else static_global_p90)
    n = seven.model_count[codes]
    weight = np.where(n >= SHRINK_COUNT, 1.0,
                      n / (n + SHRINK_COUNT))
    model_p90 = np.where(n > 0, seven.model_p90[codes], global_p90)
    return weight * model_p90 + (1.0 - weight) * global_p90


def pinball_loss(actual: np.ndarray, prediction: np.ndarray,
                 quantile: float = Q) -> np.ndarray:
    actual = np.asarray(actual, dtype=np.float64)
    prediction = np.asarray(prediction, dtype=np.float64)
    if len(actual) != len(prediction):
        raise ValueError('score_arrays_differ_in_length')
    if np.any(~np.isfinite(actual)) or np.any(~np.isfinite(prediction)):
        raise ValueError('nonfinite_score_input')
    delta = actual - prediction
    return np.where(delta >= 0, quantile * delta,
                    (1.0 - quantile) * -delta)


def summarize_score(actual: np.ndarray, prediction: np.ndarray) -> dict:
    if not len(actual):
        raise ValueError('empty_score_population')
    loss = pinball_loss(actual, prediction)
    return {'count': int(len(actual)),
            'mean_pinball_tokens': float(np.mean(loss)),
            'coverage': float(np.mean(np.asarray(actual) <= prediction)),
            'mean_predicted_tokens': float(np.mean(prediction))}
