"""Frozen Coding-to-LMM input-demand transfer and modality feature ablation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_azure_code_input_demand as coding  # noqa: E402

SOURCE = ROOT / "data/external/azure_lmm_2025/AzureLMMInferenceTrace_multimodal.csv.gz"
CODING_MODEL = ROOT / "artifacts/azure_code_input_demand_v1/relative_q90_model.joblib"
PROTOCOL = ROOT / "reports/azure_lmm_demand_transfer_protocol.md"
AUDIT_REPORT = ROOT / "reports/azure_lmm_demand_data_audit_v1.md"
TEST_FILE = ROOT / "tests/test_azure_lmm_demand_transfer.py"
CODING_SCRIPT = ROOT / "scripts/evaluate_azure_code_input_demand.py"
OUTPUT = ROOT / "artifacts/azure_lmm_demand_transfer_v1"
SOURCE_SHA256 = "1393ef21cc7e5e5a3155beea168edbb1b86b26d016464f102b580ff81e474e15"
CODING_MODEL_SHA256 = "72c3c812192abcd02cc4095d87f77b75da83935900d5f6a4e51afc37f77582b4"
START = pd.Timestamp("2024-10-15T12:00:00Z")
SLOT_NS = 300_000_000_000
N_SLOTS = 7 * 288
TRAIN = slice(12, 3 * 288)
VALIDATION_DAY = 3
ASSESSMENT_DAYS = (4, 5, 6)
MIN_SCALE = 1_000.0
Q = 0.9
PARAMS = dict(loss="quantile", quantile=Q, max_iter=150,
              learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=20,
              l2_regularization=1.0, early_stopping=False, random_state=42)
TOKEN_FEATURES = tuple(coding.FEATURES)
MODALITY_FEATURES = (
    "image_lag1_over_hour_mean", "image_lag3_over_hour_mean",
    "image_lag12_over_hour_mean", "image_hour_q90_over_hour_mean",
    "image_recent_six_over_previous_six", "images_per_request_past_hour",
    "request_lag1_over_hour_mean", "request_lag3_over_hour_mean",
    "request_lag12_over_hour_mean", "request_hour_q90_over_hour_mean",
    "request_recent_six_over_previous_six",
)
LEARNED_METHODS = (
    "coding_frozen_relative_q90_hgb",
    "lmm_token_only_relative_q90_hgb",
    "lmm_modality_relative_q90_hgb",
)
LOCAL_BASELINES = (
    "lmm_train_weighted_ratio_q90",
    "lmm_past_hour_empirical_q90",
    "lmm_previous_day_ratio",
    "lmm_previous_day_ratio_plus_train_residual_q90",
)
ALL_METHODS = LEARNED_METHODS + LOCAL_BASELINES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def code_environment() -> dict:
    return {
        "script_sha256": sha256_file(Path(__file__)),
        "test_sha256": sha256_file(TEST_FILE),
        "coding_script_sha256": sha256_file(CODING_SCRIPT),
        "python": os.sys.version.split()[0],
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "sklearn": sklearn.__version__,
        "joblib": joblib.__version__,
    }


def verify_sources() -> None:
    if not SOURCE.is_file() or sha256_file(SOURCE) != SOURCE_SHA256:
        raise ValueError("Azure LMM source is missing or its SHA-256 changed")
    if not CODING_MODEL.is_file() or sha256_file(CODING_MODEL) != CODING_MODEL_SHA256:
        raise ValueError("Frozen Azure Coding model is missing or changed")


def aggregate_source(path: Path, start: pd.Timestamp = START,
                     n_slots: int = N_SLOTS) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Read only timestamp, image count, and input-token count in bounded chunks."""
    image_totals = np.zeros(n_slots, dtype=np.int64)
    token_totals = np.zeros(n_slots, dtype=np.int64)
    request_counts = np.zeros(n_slots, dtype=np.int64)
    rows = 0
    last_time_ns = None
    min_time_ns = None
    max_time_ns = None
    end_ns = start.value + n_slots * SLOT_NS

    with pd.read_csv(
        path,
        usecols=["TIMESTAMP", "NumImages", "ContextTokens"],
        dtype={"TIMESTAMP": "string", "NumImages": "int64", "ContextTokens": "int64"},
        chunksize=500_000,
    ) as reader:
        for chunk in reader:
            if chunk.empty:
                continue
            times = pd.to_datetime(chunk["TIMESTAMP"], utc=True, errors="raise")
            time_ns = times.astype("int64").to_numpy()
            images = chunk["NumImages"].to_numpy(np.int64)
            tokens = chunk["ContextTokens"].to_numpy(np.int64)
            if (not len(time_ns) or (np.diff(time_ns) < 0).any()
                    or (last_time_ns is not None and time_ns[0] < last_time_ns)):
                raise ValueError("Azure LMM timestamps are not sorted")
            if ((time_ns < start.value).any() or (time_ns >= end_ns).any()
                    or (images < 0).any() or (tokens < 0).any()):
                raise ValueError("Azure LMM row falls outside the fixed span or has negative counts")
            slots = ((time_ns - start.value) // SLOT_NS).astype(np.int64)
            request_counts += np.bincount(slots, minlength=n_slots).astype(np.int64)
            image_totals += np.bincount(
                slots, weights=images.astype(np.float64), minlength=n_slots
            ).round().astype(np.int64)
            token_totals += np.bincount(
                slots, weights=tokens.astype(np.float64), minlength=n_slots
            ).round().astype(np.int64)
            rows += len(chunk)
            last_time_ns = int(time_ns[-1])
            chunk_min, chunk_max = int(time_ns.min()), int(time_ns.max())
            min_time_ns = chunk_min if min_time_ns is None else min(min_time_ns, chunk_min)
            max_time_ns = chunk_max if max_time_ns is None else max(max_time_ns, chunk_max)

    if rows == 0 or int(request_counts.sum()) != rows:
        raise ValueError("Azure LMM file is empty or aggregation counts do not match")
    coverage = {
        "request_rows": int(rows),
        "nonempty_five_minute_bins": int((request_counts > 0).sum()),
        "empty_five_minute_bins": int((request_counts == 0).sum()),
        "first_request_utc": pd.Timestamp(min_time_ns, tz="UTC").isoformat(),
        "last_request_utc": pd.Timestamp(max_time_ns, tz="UTC").isoformat(),
        "five_minute_bins": int(n_slots),
    }
    return token_totals, image_totals, request_counts, coverage


def build_modality_features(images: np.ndarray, requests: np.ndarray) -> pd.DataFrame:
    if len(images) != N_SLOTS or len(requests) != N_SLOTS:
        raise ValueError("modality series must cover the fixed seven-day grid")
    image_series = pd.Series(np.asarray(images, dtype=float))
    request_series = pd.Series(np.asarray(requests, dtype=float))
    image_prior = image_series.shift(1)
    request_prior = request_series.shift(1)
    image_mean = image_prior.rolling(12, min_periods=12).mean()
    request_mean = request_prior.rolling(12, min_periods=12).mean()

    def normalized(values: pd.Series, denominator: pd.Series) -> pd.Series:
        return (values / denominator.where(denominator > 0)).replace(
            [np.inf, -np.inf], np.nan
        ).fillna(0.0)

    image_recent = image_prior.rolling(6, min_periods=6).mean()
    image_older = image_series.shift(7).rolling(6, min_periods=6).mean()
    request_recent = request_prior.rolling(6, min_periods=6).mean()
    request_older = request_series.shift(7).rolling(6, min_periods=6).mean()
    image_change = normalized(image_recent, image_older)
    request_change = normalized(request_recent, request_older)
    image_q90 = image_prior.rolling(12, min_periods=12).quantile(Q)
    request_q90 = request_prior.rolling(12, min_periods=12).quantile(Q)
    image_hour = image_prior.rolling(12, min_periods=12).sum()
    request_hour = request_prior.rolling(12, min_periods=12).sum()
    image_per_request = (image_hour / request_hour.where(request_hour > 0)).fillna(0.0)

    result = pd.DataFrame({
        "image_lag1_over_hour_mean": normalized(image_series.shift(1), image_mean),
        "image_lag3_over_hour_mean": normalized(image_series.shift(3), image_mean),
        "image_lag12_over_hour_mean": normalized(image_series.shift(12), image_mean),
        "image_hour_q90_over_hour_mean": normalized(image_q90, image_mean),
        "image_recent_six_over_previous_six": image_change,
        "images_per_request_past_hour": image_per_request,
        "request_lag1_over_hour_mean": normalized(request_series.shift(1), request_mean),
        "request_lag3_over_hour_mean": normalized(request_series.shift(3), request_mean),
        "request_lag12_over_hour_mean": normalized(request_series.shift(12), request_mean),
        "request_hour_q90_over_hour_mean": normalized(request_q90, request_mean),
        "request_recent_six_over_previous_six": request_change,
    })
    return result.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_design(tokens: np.ndarray, images: np.ndarray,
                 requests: np.ndarray) -> pd.DataFrame:
    frame = coding.design_from_aggregates(tokens, start=START)
    timestamps = pd.date_range(START, periods=N_SLOTS, freq="5min")
    clock = (timestamps.hour.to_numpy() * 12
             + timestamps.minute.to_numpy() // 5)
    frame["clock_sin"] = np.sin(2 * np.pi * clock / 288)
    frame["clock_cos"] = np.cos(2 * np.pi * clock / 288)
    frame["weekend"] = (timestamps.dayofweek.to_numpy() >= 5).astype(float)
    modality = build_modality_features(images, requests)
    return pd.concat([frame.reset_index(drop=True),
                      modality.reset_index(drop=True)], axis=1)


def fit_lmm_models(frame: pd.DataFrame) -> tuple[dict, dict[str, np.ndarray]]:
    train = frame.iloc[TRAIN]
    supported = train["scale"].to_numpy(float) >= MIN_SCALE
    if supported.sum() < math.ceil(0.95 * len(train)):
        raise ValueError("LMM train support fell below the frozen 95% floor")
    train = train.loc[supported]
    scale = train["scale"].to_numpy(float)
    target = train["ratio"].to_numpy(float)
    weights = scale / scale.mean()
    global_q90 = coding.weighted_quantile(target, weights, Q)
    residual = train["ratio"].to_numpy(float) - train["previous_day_ratio"].to_numpy(float)
    residual_ok = np.isfinite(residual)
    if residual_ok.sum() < 100:
        raise ValueError("too few LMM training previous-day residuals")
    residual_q90 = coding.weighted_quantile(
        residual[residual_ok], weights[residual_ok], Q
    )

    token_model = HistGradientBoostingRegressor(**PARAMS)
    token_model.fit(train[list(TOKEN_FEATURES)].to_numpy(float), target,
                    sample_weight=weights)
    modality_model = HistGradientBoostingRegressor(**PARAMS)
    modality_model.fit(
        train[list(TOKEN_FEATURES + MODALITY_FEATURES)].to_numpy(float),
        target,
        sample_weight=weights,
    )

    bundle = {
        "token_model": token_model,
        "modality_model": modality_model,
        "global_ratio_q90": float(global_q90),
        "previous_day_ratio_residual_q90": float(residual_q90),
        "training_mean_scale": float(scale.mean()),
        "training_bins": int(len(train)),
        "token_features": TOKEN_FEATURES,
        "modality_features": MODALITY_FEATURES,
        "parameters": PARAMS,
    }
    coding_bundle = joblib.load(CODING_MODEL)
    coding_predictions = coding.predict_from_bundle(coding_bundle, frame)
    all_scale = frame["scale"].to_numpy(float)
    past_day = frame["previous_day_ratio"].to_numpy(float)
    has_past_day = np.isfinite(past_day)
    safe_past_day = np.where(has_past_day, past_day, global_q90)

    token_raw = token_model.predict(frame[list(TOKEN_FEATURES)].to_numpy(float))
    modality_raw = modality_model.predict(
        frame[list(TOKEN_FEATURES + MODALITY_FEATURES)].to_numpy(float)
    )
    predictions = {
        "coding_frozen_relative_q90_hgb": coding_predictions["relative_q90_hgb"],
        "lmm_token_only_relative_q90_hgb": np.maximum(0.0, token_raw) * all_scale,
        "lmm_modality_relative_q90_hgb": np.maximum(0.0, modality_raw) * all_scale,
        "lmm_train_weighted_ratio_q90": np.full(N_SLOTS, global_q90) * all_scale,
        "lmm_past_hour_empirical_q90": frame[
            "past_hour_empirical_q90"
        ].to_numpy(float),
        "lmm_previous_day_ratio": np.maximum(0.0, safe_past_day) * all_scale,
        "lmm_previous_day_ratio_plus_train_residual_q90": np.where(
            has_past_day,
            np.maximum(0.0, past_day + residual_q90),
            global_q90,
        ) * all_scale,
    }
    return bundle, predictions


def metric(y: np.ndarray, predicted: np.ndarray) -> dict:
    if (not len(y) or not np.isfinite(y).all() or not np.isfinite(predicted).all()
            or (y < 0).any() or (predicted < 0).any()):
        raise ValueError("invalid values in LMM forecast metric")
    difference = y - predicted
    pinball = np.maximum(Q * difference, (Q - 1.0) * difference)
    return {
        "eligible_bins": int(len(y)),
        "absolute_token_pinball": float(pinball.mean()),
        "coverage": float(np.mean(y <= predicted)),
        "underprediction_fraction": float(np.mean(y > predicted)),
        "mean_predicted_tokens": float(predicted.mean()),
        "mean_observed_tokens": float(y.mean()),
    }


def score_days(frame: pd.DataFrame, predictions: dict[str, np.ndarray],
               days: tuple[int, ...]) -> dict:
    per_day = {}
    for day in days:
        left, right = day * 288, (day + 1) * 288
        subset = frame.iloc[left:right]
        mask = (subset["scale"].to_numpy(float) >= MIN_SCALE)
        block_start = START + pd.Timedelta(days=day)
        block_end = block_start + pd.Timedelta(days=1)
        key = block_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        per_day[key] = {
            "day_index": int(day),
            "block_start_utc": block_start.isoformat(),
            "block_end_utc": block_end.isoformat(),
            "eligible_bins": int(mask.sum()),
            "ineligible_bins": int((~mask).sum()),
            "methods": {
                name: metric(
                    subset["y"].to_numpy(float)[mask],
                    value[left:right][mask],
                )
                for name, value in predictions.items()
            },
        }
    daily_loss = {
        name: float(np.mean([
            record["methods"][name]["absolute_token_pinball"]
            for record in per_day.values()
        ]))
        for name in ALL_METHODS
    }
    return {"per_day": per_day,
            "equal_day_mean_absolute_token_pinball": daily_loss}


def pre_score_support(frame: pd.DataFrame, coverage: dict) -> dict:
    train = frame.iloc[TRAIN]
    validation = frame.iloc[VALIDATION_DAY * 288:(VALIDATION_DAY + 1) * 288]
    assessment = [
        frame.iloc[day * 288:(day + 1) * 288] for day in ASSESSMENT_DAYS
    ]
    train_eligible = int((train["scale"] >= MIN_SCALE).sum())
    validation_eligible = int((validation["scale"] >= MIN_SCALE).sum())
    assessment_eligible = {
        (START + pd.Timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ"):
        int((frame.iloc[day * 288:(day + 1) * 288]["scale"] >= MIN_SCALE).sum())
        for day in ASSESSMENT_DAYS
    }
    checks = {
        "training_at_least_95pct": train_eligible >= math.ceil(0.95 * len(train)),
        "validation_at_least_95pct": validation_eligible >= math.ceil(0.95 * len(validation)),
        "each_assessment_day_at_least_95pct": all(
            value >= math.ceil(0.95 * 288)
            for value in assessment_eligible.values()
        ),
        "fixed_seven_day_grid": coverage["five_minute_bins"] == N_SLOTS,
    }
    return {
        "all_passed": bool(all(checks.values())),
        "checks": checks,
        "training_eligible_bins": train_eligible,
        "training_candidate_bins": int(len(train)),
        "validation_eligible_bins": validation_eligible,
        "validation_candidate_bins": int(len(validation)),
        "assessment_eligible_bins": assessment_eligible,
        "request_rows": coverage["request_rows"],
        "nonempty_five_minute_bins": coverage["nonempty_five_minute_bins"],
        "empty_five_minute_bins": coverage["empty_five_minute_bins"],
        "first_request_utc": coverage["first_request_utc"],
        "last_request_utc": coverage["last_request_utc"],
    }


def assessment_gate(assessment: dict, support: dict) -> dict:
    scores = assessment["equal_day_mean_absolute_token_pinball"]
    proposed = scores["lmm_modality_relative_q90_hgb"]
    checks = {
        "at_least_5pct_below_frozen_coding_transfer":
            proposed <= 0.95 * scores["coding_frozen_relative_q90_hgb"],
        "at_least_5pct_below_lmm_token_only_ablation":
            proposed <= 0.95 * scores["lmm_token_only_relative_q90_hgb"],
    }
    checks.update({
        f"at_least_5pct_below_{baseline}":
            proposed <= 0.95 * scores[baseline]
        for baseline in LOCAL_BASELINES
    })
    daily_wins = 0
    for record in assessment["per_day"].values():
        local_best = min(record["methods"][name]["absolute_token_pinball"]
                         for name in LOCAL_BASELINES)
        daily_wins += (
            record["methods"]["lmm_modality_relative_q90_hgb"][
                "absolute_token_pinball"
            ] < local_best
        )
    checks["beats_best_local_baseline_on_at_least_two_days"] = daily_wins >= 2
    checks["coverage_each_day_0_85_to_0_95"] = all(
        0.85 <= record["methods"]["lmm_modality_relative_q90_hgb"]["coverage"] <= 0.95
        for record in assessment["per_day"].values()
    )
    checks["pre_score_support_passed"] = bool(support["all_passed"])
    return {
        "all_passed": bool(all(checks.values())),
        "checks": checks,
        "daily_wins_vs_best_local_baseline": int(daily_wins),
        "failed": [name for name, passed in checks.items() if not passed],
    }


def freeze() -> dict:
    verify_sources()
    if not PROTOCOL.is_file() or not TEST_FILE.is_file() or not AUDIT_REPORT.is_file():
        raise FileNotFoundError("protocol, data audit, or test is missing")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prior_freeze_sha256 = None
    current_freeze = OUTPUT / "freeze.json"
    if current_freeze.exists():
        if any((OUTPUT / name).exists() for name in (
            "evaluation_started.json", "results.json", "support_no_go.json"
        )):
            raise FileExistsError("cannot replace a freeze after evaluation began")
        archived_freeze = OUTPUT / "freeze_superseded_before_score.json"
        if archived_freeze.exists():
            raise FileExistsError("superseded freeze archive already exists")
        prior_freeze_sha256 = sha256_file(current_freeze)
        os.replace(current_freeze, archived_freeze)
    record = {
        "status": "FROZEN_BEFORE_LMM_BIN_AGGREGATION_AND_SCORE",
        "prior_superseded_freeze_sha256": prior_freeze_sha256,
        "data_audit_sha256": sha256_file(AUDIT_REPORT),
        "source_sha256": SOURCE_SHA256,
        "coding_model_sha256": CODING_MODEL_SHA256,
        "protocol_sha256": sha256_file(PROTOCOL),
        "code_environment": code_environment(),
        "target": "next five-minute Azure LMM public-sample ContextTokens sum, q90",
        "training_bins": "2024-10-15T12:00 through 2024-10-18T12:00 UTC; first 12 bins burn-in",
        "validation_bins": "2024-10-18T12:00 through 2024-10-19T12:00 UTC",
        "assessment_bins": "2024-10-19T12:00 through 2024-10-22T12:00 UTC",
        "token_features": TOKEN_FEATURES,
        "modality_features": MODALITY_FEATURES,
        "methods": ALL_METHODS,
        "model_parameters": PARAMS,
        "min_prior_hour_mean_tokens": MIN_SCALE,
        "known_exposure": "source integrity and timestamp-range audit completed; no five-minute demand aggregates or model scores inspected",
    }
    atomic_json(OUTPUT / "freeze.json", record)
    return record


def evaluate_once() -> dict:
    frozen_path = OUTPUT / "freeze.json"
    if not frozen_path.is_file():
        raise FileNotFoundError("run the freeze command first")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if (frozen["status"] != "FROZEN_BEFORE_LMM_BIN_AGGREGATION_AND_SCORE"
            or frozen["data_audit_sha256"] != sha256_file(AUDIT_REPORT)
            or frozen["source_sha256"] != SOURCE_SHA256
            or frozen["coding_model_sha256"] != CODING_MODEL_SHA256
            or frozen["protocol_sha256"] != sha256_file(PROTOCOL)
            or frozen["code_environment"] != code_environment()):
        raise ValueError("experiment freeze, source, or environment changed")
    if (OUTPUT / "evaluation_started.json").exists():
        raise FileExistsError("one-time LMM evaluation already started")
    verify_sources()
    tokens, images, requests, coverage = aggregate_source(SOURCE)
    frame = build_design(tokens, images, requests)
    support = pre_score_support(frame, coverage)
    if not support["all_passed"]:
        failure = {
            "status": "LMM_SUPPORT_NO_GO_NO_MODEL_SCORE",
            "freeze_sha256": sha256_file(frozen_path),
            "source_sha256": SOURCE_SHA256,
            "pre_score_support": support,
        }
        atomic_json(OUTPUT / "support_no_go.json", failure)
        return failure

    atomic_json(OUTPUT / "evaluation_started.json", {
        "status": "ONE_TIME_LMM_SCORE_STARTED",
        "freeze_sha256": sha256_file(frozen_path),
        "source_sha256": SOURCE_SHA256,
    })
    fitted, forecasts = fit_lmm_models(frame)
    token_model_path = OUTPUT / "lmm_token_only_model.joblib"
    modality_model_path = OUTPUT / "lmm_modality_model.joblib"
    for path, model in (
        (token_model_path, fitted["token_model"]),
        (modality_model_path, fitted["modality_model"]),
    ):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            joblib.dump(model, temporary, compress=3)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    validation = score_days(frame, forecasts, (VALIDATION_DAY,))
    assessment = score_days(frame, forecasts, ASSESSMENT_DAYS)
    gate = assessment_gate(assessment, support)
    result = {
        "status": "EXPLORATORY_GO" if gate["all_passed"] else "EXPLORATORY_NO_GO",
        "scope": "Azure LMM public sampled request trace; no GPU count, user SLO, or scale-action outcome",
        "source_sha256": SOURCE_SHA256,
        "coding_model_sha256": CODING_MODEL_SHA256,
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(Path(__file__)),
        "test_sha256": sha256_file(TEST_FILE),
        "coding_script_sha256": sha256_file(CODING_SCRIPT),
        "freeze_sha256": sha256_file(frozen_path),
        "token_model_sha256": sha256_file(token_model_path),
        "modality_model_sha256": sha256_file(modality_model_path),
        "code_environment": code_environment(),
        "source_coverage": coverage,
        "pre_score_support": support,
        "training_global_ratio_q90": fitted["global_ratio_q90"],
        "training_previous_day_ratio_residual_q90":
            fitted["previous_day_ratio_residual_q90"],
        "training_mean_scale": fitted["training_mean_scale"],
        "validation": validation,
        "assessment": assessment,
        "assessment_gate": gate,
        "blind_or_independent_test": False,
        "generated_tokens_used": False,
        "prompt_content_available": False,
    }
    atomic_json(OUTPUT / "results.json", result)
    atomic_json(OUTPUT / "manifest.json", {
        "source_sha256": SOURCE_SHA256,
        "coding_model_sha256": CODING_MODEL_SHA256,
        "protocol_sha256": result["protocol_sha256"],
        "script_sha256": result["script_sha256"],
        "test_sha256": result["test_sha256"],
        "coding_script_sha256": result["coding_script_sha256"],
        "freeze_sha256": result["freeze_sha256"],
        "token_model_sha256": result["token_model_sha256"],
        "modality_model_sha256": result["modality_model_sha256"],
        "results_sha256": sha256_file(OUTPUT / "results.json"),
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "evaluate_once"))
    args = parser.parse_args()
    result = freeze() if args.command == "freeze" else evaluate_once()
    print(json.dumps({
        "status": result["status"],
        "assessment_gate": result.get("assessment_gate"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
