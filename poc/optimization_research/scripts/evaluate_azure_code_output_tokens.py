"""Staged, frozen Azure Coding output-token p90 experiment.

The audit stage never interprets GeneratedTokens. Prepare stops at the first
test-day row before parsing its output. Test verifies the frozen manifest before
reading held-out outputs.
"""

from __future__ import annotations

import argparse
from array import array
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/azure_llm_2024/AzureLLMInferenceTrace_code_1week.csv"
PROTOCOL = ROOT / "reports/azure_code_output_tokens_protocol.md"
OUTPUT = ROOT / "artifacts/azure_code_output_tokens_v1"
OFFICIAL_BYTES = 691_989_454
OFFICIAL_SHA256 = "71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f"
PROTOCOL_SHA256 = "916f1e156559ca140b1397931f52b6078ba61f58e38e94affa7ffb2f8f787570"
HEADER = b"TIMESTAMP,ContextTokens,GeneratedTokens"
DAY_ZERO = date(2024, 5, 10)
Q = 0.9
MIN_SUPPORT = 100
HGB_ROWS_PER_DAY = 50_000
METHODS = (
    "rolling_bin", "rolling_global", "static_hgb_length",
    "static_bin", "static_global",
)
HGB_PARAMS = {
    "loss": "quantile", "quantile": Q, "max_iter": 140,
    "max_leaf_nodes": 15, "min_samples_leaf": 60,
    "l2_regularization": 10, "learning_rate": 0.05,
    "early_stopping": False, "random_state": 42,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def verify_source() -> None:
    if SOURCE.stat().st_size != OFFICIAL_BYTES:
        raise ValueError("Azure Coding asset byte count differs from official release")
    if sha256(SOURCE) != OFFICIAL_SHA256:
        raise ValueError("Azure Coding asset SHA-256 differs from official release")


def verify_protocol() -> None:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Protocol changed after pre-download freeze")


def check_header(handle) -> None:
    if handle.readline().rstrip(b"\r\n") != HEADER:
        raise ValueError("Unexpected Azure CSV header")


def parse_timestamp(raw: bytes) -> tuple[int, datetime]:
    try:
        timestamp = datetime.fromisoformat(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Malformed timestamp") from exc
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError("Timestamp is not UTC")
    day = (timestamp.date() - DAY_ZERO).days
    if not 0 <= day <= 6:
        raise ValueError(f"Timestamp outside expected seven days: {timestamp}")
    return day, timestamp


def parse_token(raw: bytes, name: str) -> int:
    if not raw.isdigit():
        raise ValueError(f"Missing, negative, or noninteger {name}")
    value = int(raw)
    if value > 2**63 - 1:
        raise ValueError(f"{name} exceeds int64")
    return value


def split_timestamp(raw: bytes) -> tuple[int, datetime, bytes]:
    ts_raw, comma, remaining = raw.rstrip(b"\r\n").partition(b",")
    if not comma:
        raise ValueError("Malformed CSV row")
    day, timestamp = parse_timestamp(ts_raw)
    return day, timestamp, remaining


def audit_inputs() -> dict:
    verify_protocol()
    verify_source()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "input_audit.json"
    if target.exists():
        raise FileExistsError(f"Input audit already exists: {target}")
    counts = [0] * 7
    eligible = [0] * 7
    prev_timestamp = None
    first = last = None
    with SOURCE.open("rb") as handle:
        check_header(handle)
        for raw in handle:
            day, timestamp, remainder = split_timestamp(raw)
            input_raw, comma, _unused_output = remainder.partition(b",")
            if not comma:
                raise ValueError("Malformed CSV row")
            context_tokens = parse_token(input_raw, "ContextTokens")
            if prev_timestamp is not None and timestamp < prev_timestamp:
                raise ValueError("Rows are not sorted by timestamp")
            prev_timestamp = timestamp
            if first is None:
                first = timestamp.isoformat()
            last = timestamp.isoformat()
            counts[day] += 1
            if context_tokens > 0:
                eligible[day] += 1
    if not all(counts) or first is None:
        raise ValueError("One or more Azure Coding days are empty")
    audit = {
        "source_sha256": OFFICIAL_SHA256,
        "source_bytes": OFFICIAL_BYTES,
        "protocol_sha256": PROTOCOL_SHA256,
        "first_timestamp_utc": first,
        "last_timestamp_utc": last,
        "rows_by_day": counts,
        "eligible_context_positive_by_day": eligible,
        "total_rows": sum(counts),
        "total_eligible": sum(eligible),
        "labels_inspected": False,
    }
    write_json(target, audit)
    return audit


def higher_quantile(values: np.ndarray) -> float:
    if not len(values):
        raise ValueError("Empty quantile group")
    return float(np.quantile(values, Q, method="higher"))


def count_hist(values: np.ndarray) -> dict[int, int]:
    if len(values) == 0:
        return {}
    unique, counts = np.unique(values, return_counts=True)
    return {int(value): int(count) for value, count in zip(unique, counts)}


def add_hist(target: dict[int, int], source: dict[int, int]) -> None:
    for value, n in source.items():
        target[value] = target.get(value, 0) + n


def hist_p90(hist: dict[int, int]) -> float:
    n = sum(hist.values())
    if n <= 0:
        raise ValueError("Empty historical output distribution")
    index = math.ceil(Q * (n - 1))
    seen = 0
    for value, count in sorted(hist.items()):
        seen += count
        if seen > index:
            return float(value)
    raise AssertionError("Histogram quantile failed")


def make_day_bins(
    context: np.ndarray, output: np.ndarray, edges: np.ndarray
) -> list[dict[int, int]]:
    bins = np.searchsorted(edges, context, side="left")
    return [count_hist(output[bins == b]) for b in range(len(edges) + 1)]


def encode_history(day_bins: dict[int, list[dict[int, int]]]) -> dict:
    return {
        str(day): [
            {str(value): n for value, n in histogram.items()}
            for histogram in bins
        ]
        for day, bins in sorted(day_bins.items())
    }


def decode_history(encoded: dict) -> dict[int, list[dict[int, int]]]:
    return {
        int(day): [
            {int(value): int(n) for value, n in histogram.items()}
            for histogram in bins
        ]
        for day, bins in encoded.items()
    }


def combine_history(
    by_day: dict[int, list[dict[int, int]]], days: range
) -> tuple[dict[int, int], list[dict[int, int]]]:
    if any(day not in by_day for day in days):
        raise ValueError("Missing available historical day")
    n_bins = len(by_day[next(iter(days))])
    global_hist = {}
    bin_hists = [{} for _ in range(n_bins)]
    for day in days:
        if len(by_day[day]) != n_bins:
            raise ValueError("Inconsistent number of bins")
        for b, histogram in enumerate(by_day[day]):
            add_hist(global_hist, histogram)
            add_hist(bin_hists[b], histogram)
    return global_hist, bin_hists


def prepare() -> dict:
    verify_protocol()
    verify_source()
    audit_path = OUTPUT / "input_audit.json"
    audit = json.loads(audit_path.read_text())
    if audit["source_sha256"] != OFFICIAL_SHA256 or audit["protocol_sha256"] != PROTOCOL_SHA256:
        raise ValueError("Input audit is from another source or protocol")
    model_path = OUTPUT / "static_hgb.joblib"
    history_path = OUTPUT / "train_histograms.json"
    manifest_path = OUTPUT / "manifest.json"
    if any(path.exists() for path in (model_path, history_path, manifest_path)):
        raise FileExistsError("Prepared artifacts already exist; use a new version")
    inputs = [array("q") for _ in range(4)]
    labels = [array("q") for _ in range(4)]
    seen_rows = [0] * 4
    with SOURCE.open("rb") as handle:
        check_header(handle)
        prev_timestamp = None
        for raw in handle:
            day, timestamp, remainder = split_timestamp(raw)
            if day >= 4:
                break  # held-out output bytes are never parsed in prepare
            if prev_timestamp is not None and timestamp < prev_timestamp:
                raise ValueError("Training rows are not sorted")
            prev_timestamp = timestamp
            input_raw, comma, output_raw = remainder.partition(b",")
            if not comma:
                raise ValueError("Malformed training CSV row")
            context_tokens = parse_token(input_raw, "training ContextTokens")
            output_tokens = parse_token(output_raw, "training GeneratedTokens")
            seen_rows[day] += 1
            if context_tokens > 0:
                inputs[day].append(context_tokens)
                labels[day].append(output_tokens)
    if seen_rows != audit["rows_by_day"][:4]:
        raise ValueError("Training day row counts changed after input audit")
    if [len(part) for part in inputs] != audit["eligible_context_positive_by_day"][:4]:
        raise ValueError("Training eligible counts changed after input audit")
    train_x = np.concatenate([np.frombuffer(part, dtype=np.int64) for part in inputs])
    train_y = np.concatenate([np.frombuffer(part, dtype=np.int64) for part in labels])
    edges = np.unique(np.quantile(train_x, [0.2, 0.4, 0.6, 0.8], method="higher"))
    static_global = higher_quantile(train_y)
    edges = edges.astype(np.int64)
    hist_by_day = {
        day: make_day_bins(
            np.frombuffer(inputs[day], dtype=np.int64),
            np.frombuffer(labels[day], dtype=np.int64), edges,
        )
        for day in range(4)
    }
    global_hist, bin_hists = combine_history(hist_by_day, range(4))
    if static_global != hist_p90(global_hist):
        raise AssertionError("Histogram p90 differs from array p90")
    static_bins = [
        hist_p90(hist) if sum(hist.values()) >= MIN_SUPPORT else static_global
        for hist in bin_hists
    ]
    rng = np.random.default_rng(42)
    fit_x_parts = []
    fit_y_parts = []
    sample_counts = []
    for day in range(4):
        day_x = np.frombuffer(inputs[day], dtype=np.int64)
        day_y = np.frombuffer(labels[day], dtype=np.int64)
        index = np.sort(rng.choice(len(day_x), size=min(HGB_ROWS_PER_DAY, len(day_x)), replace=False))
        fit_x_parts.append(np.log1p(day_x[index].astype(float)))
        fit_y_parts.append(day_y[index].astype(float))
        sample_counts.append(len(index))
    fit_x = np.concatenate(fit_x_parts).reshape(-1, 1)
    fit_y = np.concatenate(fit_y_parts)
    estimator = HistGradientBoostingRegressor(**HGB_PARAMS)
    estimator.fit(fit_x, fit_y)
    joblib.dump(estimator, model_path, compress=3)
    write_json(history_path, encode_history(hist_by_day))
    manifest = {
        "version": 1,
        "source_sha256": OFFICIAL_SHA256,
        "source_bytes": OFFICIAL_BYTES,
        "protocol_sha256": PROTOCOL_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "input_audit_sha256": sha256(audit_path),
        "model_sha256": sha256(model_path),
        "train_histograms_sha256": sha256(history_path),
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "split": {"train_days": [0, 1, 2, 3], "test_days": [4, 5, 6]},
        "quantile": Q,
        "bin_edges_context_tokens": edges.tolist(),
        "bin_boundary_side": "left",
        "min_bin_support": MIN_SUPPORT,
        "rolling_history": "[max(0,d-8),d-2] inclusive",
        "hgb_params": HGB_PARAMS,
        "hgb_sample_seed": 42,
        "hgb_sample_count_by_train_day": sample_counts,
        "train_rows_by_day": seen_rows,
        "train_eligible_by_day": [len(part) for part in inputs],
        "static_global_p90": static_global,
        "static_bin_p90": static_bins,
        "methods": METHODS,
        "heldout_labels_inspected": False,
    }
    write_json(manifest_path, manifest)
    return manifest


def check_manifest() -> tuple[dict, dict[int, list[dict[int, int]]], object]:
    verify_protocol()
    verify_source()
    manifest_path = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    expected = {
        "protocol_sha256": PROTOCOL_SHA256,
        "source_sha256": OFFICIAL_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "input_audit_sha256": sha256(OUTPUT / "input_audit.json"),
        "model_sha256": sha256(OUTPUT / "static_hgb.joblib"),
        "train_histograms_sha256": sha256(OUTPUT / "train_histograms.json"),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Frozen {key} differs from manifest")
    history = decode_history(json.loads((OUTPUT / "train_histograms.json").read_text()))
    model = joblib.load(OUTPUT / "static_hgb.joblib")
    return manifest, history, model


def scores(y: np.ndarray, prediction: np.ndarray) -> dict:
    if len(y) == 0:
        raise ValueError("Empty score slice")
    difference = y.astype(float) - prediction
    loss = np.where(difference >= 0, Q * difference, (Q - 1) * difference)
    return {
        "rows": int(len(y)),
        "pinball_tokens": float(loss.mean()),
        "coverage": float(np.mean(y <= prediction)),
        "mean_budget_tokens": float(prediction.mean()),
    }


def score_day(
    day: int, context: np.ndarray, actual: np.ndarray,
    manifest: dict, history: dict[int, list[dict[int, int]]], model,
) -> dict:
    if day not in (4, 5, 6):
        raise ValueError("Unexpected test day")
    edges = np.asarray(manifest["bin_edges_context_tokens"], dtype=np.int64)
    bins = np.searchsorted(edges, context, side="left")
    available = range(max(0, day - 8), day - 1)
    global_hist, bin_hists = combine_history(history, available)
    rolling_global = hist_p90(global_hist)
    rolling_bins = np.asarray([
        hist_p90(hist) if sum(hist.values()) >= MIN_SUPPORT else rolling_global
        for hist in bin_hists
    ], dtype=float)
    predictions = {
        "rolling_bin": rolling_bins[bins],
        "rolling_global": np.full(len(actual), rolling_global, dtype=float),
        "static_hgb_length": np.maximum(
            0.0, model.predict(np.log1p(context.astype(float)).reshape(-1, 1))
        ),
        "static_bin": np.asarray(manifest["static_bin_p90"], dtype=float)[bins],
        "static_global": np.full(len(actual), manifest["static_global_p90"], dtype=float),
    }
    per_method = {name: scores(actual, predictions[name]) for name in METHODS}
    per_bin = {
        str(b): {
            "rows": int(np.count_nonzero(mask)),
            "methods": {name: scores(actual[mask], pred[mask]) for name, pred in predictions.items()},
        }
        for b in range(len(edges) + 1)
        if (mask := bins == b).any()
    }
    return {
        "day": day,
        "date_utc": (DAY_ZERO + timedelta(days=day)).isoformat(),
        "rows": int(len(actual)),
        "history_days": list(available),
        "methods": per_method,
        "by_bin": per_bin,
    }


def summarize(daily: list[dict]) -> dict:
    total_rows = sum(day["rows"] for day in daily)
    methods = {}
    for method in METHODS:
        by_day = [day["methods"][method] for day in daily]
        methods[method] = {
            "rows": total_rows,
            "equal_day_pinball_tokens": float(np.mean([x["pinball_tokens"] for x in by_day])),
            "row_weighted_pinball_tokens": float(sum(
                x["rows"] * x["pinball_tokens"] for x in by_day
            ) / total_rows),
            "coverage": float(sum(x["rows"] * x["coverage"] for x in by_day) / total_rows),
            "mean_budget_tokens": float(sum(
                x["rows"] * x["mean_budget_tokens"] for x in by_day
            ) / total_rows),
        }
    primary = "rolling_bin"
    gates = {
        "lower_equal_day_loss_than_all_comparators": all(
            methods[primary]["equal_day_pinball_tokens"] < methods[name]["equal_day_pinball_tokens"]
            for name in METHODS if name != primary
        ),
        "daily_wins_vs_rolling_global_at_least_two": sum(
            day["methods"][primary]["pinball_tokens"]
            < day["methods"]["rolling_global"]["pinball_tokens"]
            for day in daily
        ) >= 2,
        "coverage_at_least_90pct_overall_and_each_day": (
            methods[primary]["coverage"] >= Q
            and all(day["methods"][primary]["coverage"] >= Q for day in daily)
        ),
    }
    gates["all_pass"] = all(gates.values())
    return {"rows": total_rows, "methods": methods, "predeclared_gates": gates}


def frozen_test() -> dict:
    target = OUTPUT / "heldout_scores.json"
    if target.exists():
        raise FileExistsError("Held-out scores already exist; do not re-score this version")
    manifest, history, model = check_manifest()
    audit = json.loads((OUTPUT / "input_audit.json").read_text())
    daily = []
    seen_rows = [0] * 7
    eligible_rows = [0] * 7
    current_day = None
    context = array("q")
    output = array("q")
    prev_timestamp = None

    def finish(day: int) -> None:
        x = np.frombuffer(context, dtype=np.int64)
        y = np.frombuffer(output, dtype=np.int64)
        if len(x) == 0:
            raise ValueError(f"Empty held-out day {day}")
        daily.append(score_day(day, x, y, manifest, history, model))
        history[day] = make_day_bins(x, y, np.asarray(
            manifest["bin_edges_context_tokens"], dtype=np.int64
        ))

    with SOURCE.open("rb") as handle:
        check_header(handle)
        for raw in handle:
            day, timestamp, remainder = split_timestamp(raw)
            if prev_timestamp is not None and timestamp < prev_timestamp:
                raise ValueError("Rows are not sorted")
            prev_timestamp = timestamp
            if current_day is not None and day != current_day:
                finish(current_day)  # score previous day before parsing this day's label
                context = array("q")
                output = array("q")
            if day >= 4:
                current_day = day
            seen_rows[day] += 1
            input_raw, comma, output_raw = remainder.partition(b",")
            if not comma:
                raise ValueError("Malformed CSV row")
            input_tokens = parse_token(input_raw, "ContextTokens")
            if input_tokens > 0:
                eligible_rows[day] += 1
            if day >= 4:
                output_tokens = parse_token(output_raw, "held-out GeneratedTokens")
                if input_tokens > 0:
                    context.append(input_tokens)
                    output.append(output_tokens)
    if current_day is not None:
        finish(current_day)
    if [day["day"] for day in daily] != [4, 5, 6]:
        raise ValueError("Missing held-out day")
    if seen_rows != audit["rows_by_day"] or eligible_rows != audit["eligible_context_positive_by_day"]:
        raise ValueError("Test day input counts differ from pre-freeze audit")
    result = {
        "manifest_sha256": sha256(OUTPUT / "manifest.json"),
        "source_sha256": OFFICIAL_SHA256,
        "protocol_sha256": PROTOCOL_SHA256,
        "daily": daily,
        "summary": summarize(daily),
        "labels_inspected_after_freeze": True,
    }
    write_json(target, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("audit-inputs", "prepare", "test"))
    args = parser.parse_args()
    if args.stage == "audit-inputs":
        result = audit_inputs()
        print(json.dumps(result, indent=2))
    elif args.stage == "prepare":
        result = prepare()
        print(json.dumps({
            "manifest_sha256": sha256(OUTPUT / "manifest.json"),
            "source_sha256": result["source_sha256"],
            "protocol_sha256": result["protocol_sha256"],
            "script_sha256": result["script_sha256"],
            "bin_edges_context_tokens": result["bin_edges_context_tokens"],
            "train_eligible_by_day": result["train_eligible_by_day"],
            "hgb_sample_count_by_train_day": result["hgb_sample_count_by_train_day"],
            "heldout_labels_inspected": False,
        }, indent=2))
    else:
        result = frozen_test()
        print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
