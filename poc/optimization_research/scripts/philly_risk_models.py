"""Feature and simple-model definitions for the Philly risk experiment.

Callers must pass only observed-label training rows to ``fit``. This module
never reads the source trace or an evaluation split on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.preprocessing import StandardScaler


HIGH_THRESHOLD = 80.0
REGULARIZATION_GRID = (0.1, 1.0, 10.0)
BASELINE_KEYS = (
    ("prevalence", ()),
    ("current_state", ("current_high",)),
    ("state_recent", ("current_high", "any_recent_high")),
    ("state_recent_gap", ("current_high", "any_recent_high", "past_gap_s")),
    ("state_recent_mask", ("current_high", "any_recent_high",
                           "older_valid", "middle_valid")),
)
BASE_FEATURES = (
    "older_util_filled", "middle_util_filled", "current_util",
    "history_mean", "history_max", "current_minus_oldest",
    "current_high", "any_recent_high",
)
FULL_FEATURES = BASE_FEATURES + ("older_valid", "middle_valid", "past_gap_s")
REQUIRED_COLUMNS = (
    "older_util", "middle_util", "current_util", "older_valid",
    "middle_valid", "past_gap_s", "current_high", "any_recent_high",
)


def _require_rows(frame: pd.DataFrame, *, labels: bool = False) -> None:
    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing risk-model columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Cannot fit or score an empty risk-model frame")
    current = frame["current_util"].to_numpy(dtype=float)
    if not np.isfinite(current).all() or not ((0 <= current) & (current <= 100)).all():
        raise ValueError("Current utilization must be finite and in [0, 100]")
    if labels:
        if "y" not in frame:
            raise ValueError("Observed binary risk labels are required")
        y = frame["y"].to_numpy()
        if not np.isin(y, [0, 1]).all():
            raise ValueError("Only observed binary risk labels may enter model fitting")
    for field, flag in (("older_util", "older_valid"),
                        ("middle_util", "middle_valid")):
        values = frame[field].to_numpy(dtype=float)
        valid = frame[flag].to_numpy(dtype=bool)
        if not np.isfinite(values[valid]).all():
            raise ValueError(f"{field} is nonfinite although its validity flag is true")
        if not ((0 <= values[valid]) & (values[valid] <= 100)).all():
            raise ValueError(f"{field} outside [0, 100]")
    if not np.array_equal(frame["current_high"].to_numpy(dtype=bool),
                          current >= HIGH_THRESHOLD):
        raise ValueError("current_high disagrees with current utilization")
    gap = frame["past_gap_s"].to_numpy(dtype=int)
    older = frame["older_valid"].to_numpy(dtype=bool)
    middle = frame["middle_valid"].to_numpy(dtype=bool)
    expected_gap = np.where(middle, 300, np.where(older, 600, 900))
    if not np.array_equal(gap, expected_gap):
        raise ValueError("past_gap_s disagrees with the observed masks")
    old_values = frame["older_util"].to_numpy(dtype=float)
    middle_values = frame["middle_util"].to_numpy(dtype=float)
    expected_recent = ((current >= HIGH_THRESHOLD)
                       | (older & (old_values >= HIGH_THRESHOLD))
                       | (middle & (middle_values >= HIGH_THRESHOLD)))
    if not np.array_equal(frame["any_recent_high"].to_numpy(dtype=bool), expected_recent):
        raise ValueError("any_recent_high disagrees with valid observed history")


@dataclass
class FeatureTransform:
    """Training-only median fill and standardization with explicit features."""

    include_quality: bool
    older_median: float | None = None
    middle_median: float | None = None
    scaler: StandardScaler | None = None

    @property
    def columns(self) -> tuple[str, ...]:
        return FULL_FEATURES if self.include_quality else BASE_FEATURES

    def fit(self, train: pd.DataFrame) -> "FeatureTransform":
        _require_rows(train, labels=True)
        medians = []
        for field, flag in (("older_util", "older_valid"),
                            ("middle_util", "middle_valid")):
            observed = train.loc[train[flag].astype(bool), field].to_numpy(dtype=float)
            if not len(observed):
                raise ValueError(f"No training observation for {field} median")
            medians.append(float(np.median(observed)))
        self.older_median, self.middle_median = medians
        self.scaler = StandardScaler().fit(self._raw_features(train))
        return self

    def _raw_features(self, frame: pd.DataFrame) -> np.ndarray:
        _require_rows(frame)
        if self.older_median is None or self.middle_median is None:
            raise RuntimeError("FeatureTransform must be fitted on training rows")
        old = np.where(frame["older_valid"].to_numpy(dtype=bool),
                       frame["older_util"].to_numpy(dtype=float), self.older_median)
        middle = np.where(frame["middle_valid"].to_numpy(dtype=bool),
                          frame["middle_util"].to_numpy(dtype=float), self.middle_median)
        current = frame["current_util"].to_numpy(dtype=float)
        base = np.column_stack((
            old, middle, current, (old + middle + current) / 3.0,
            np.maximum.reduce((old, middle, current)), current - old,
            frame["current_high"].to_numpy(dtype=float),
            frame["any_recent_high"].to_numpy(dtype=float),
        ))
        if self.include_quality:
            base = np.column_stack((
                base, frame["older_valid"].to_numpy(dtype=float),
                frame["middle_valid"].to_numpy(dtype=float),
                frame["past_gap_s"].to_numpy(dtype=float),
            ))
        if not np.isfinite(base).all():
            raise ValueError("Nonfinite engineered risk features")
        return base

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        if self.scaler is None:
            raise RuntimeError("FeatureTransform must be fitted before transform")
        return self.scaler.transform(self._raw_features(frame))


@dataclass
class FittedLogistic:
    transform: FeatureTransform
    estimator: LogisticRegression
    c: float
    validation_brier: float

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(self.transform.transform(frame))[:, 1]


def select_logistic(train: pd.DataFrame, validation: pd.DataFrame,
                    *, include_quality: bool) -> FittedLogistic:
    """Choose C using only validation Brier; ties favor the smaller C."""
    _require_rows(train, labels=True)
    _require_rows(validation, labels=True)
    if set(train["y"].unique()) != {0, 1}:
        raise ValueError("Training stratum must contain both binary classes")
    transform = FeatureTransform(include_quality).fit(train)
    x_train = transform.transform(train)
    x_val = transform.transform(validation)
    y_train = train["y"].to_numpy(dtype=int)
    y_val = validation["y"].to_numpy(dtype=int)
    chosen: FittedLogistic | None = None
    for c in REGULARIZATION_GRID:
        estimator = LogisticRegression(C=c, solver="lbfgs", max_iter=1000,
                                       class_weight=None, random_state=42)
        estimator.fit(x_train, y_train)
        score = float(brier_score_loss(y_val, estimator.predict_proba(x_val)[:, 1]))
        if chosen is None or score < chosen.validation_brier:
            chosen = FittedLogistic(transform, estimator, c, score)
    assert chosen is not None
    return chosen


@dataclass
class BetaTable:
    name: str
    keys: tuple[str, ...]
    probabilities: dict[tuple, float]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        missing = set(self.keys) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing Beta keys: {sorted(missing)}")
        if not self.keys:
            return np.full(len(frame), self.probabilities[()], dtype=float)
        return np.fromiter((self.probabilities.get(tuple(values), 0.5)
                            for values in frame[list(self.keys)].itertuples(index=False, name=None)),
                           dtype=float, count=len(frame))


def fit_beta_table(train_cohort: pd.DataFrame, name: str,
                   keys: Sequence[str]) -> BetaTable:
    """Fit Beta(1,1)-smoothed event rates within one training cohort."""
    _require_rows(train_cohort, labels=True)
    keys = tuple(keys)
    if not keys:
        y = train_cohort["y"].to_numpy(dtype=int)
        return BetaTable(name, (), {(): float((y.sum() + 1) / (len(y) + 2))})
    counts = train_cohort.groupby(list(keys), dropna=False)["y"].agg(["sum", "count"])
    probabilities = {}
    for values, row in counts.iterrows():
        values = values if isinstance(values, tuple) else (values,)
        probabilities[values] = float((row["sum"] + 1) / (row["count"] + 2))
    return BetaTable(name, keys, probabilities)


def select_beta_baseline(train_cohort: pd.DataFrame,
                         validation_stratum: pd.DataFrame
                         ) -> tuple[BetaTable, dict[str, float]]:
    """Choose the strongest simple table on one cohort×state validation set."""
    _require_rows(train_cohort, labels=True)
    _require_rows(validation_stratum, labels=True)
    y = validation_stratum["y"].to_numpy(dtype=int)
    scores: dict[str, float] = {}
    selected: BetaTable | None = None
    for name, keys in BASELINE_KEYS:
        table = fit_beta_table(train_cohort, name, keys)
        score = float(brier_score_loss(y, table.predict(validation_stratum)))
        scores[name] = score
        if selected is None or score < scores[selected.name]:
            selected = table
    assert selected is not None
    return selected, scores
