"""Score-free eligibility audit for machine-wide Philly GPU idle rebound.

This script never fits a model or writes row-level labels. The disjoint-machine
holdout stays locked until a separate model-selection manifest exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/philly_idle_rebound_protocol.md"
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_philly_idle_rebound_audit.py"
FREEZE = ROOT / "artifacts/philly_idle_rebound_v1/freeze_manifest.json"
DEVELOPMENT_ELIGIBILITY = ROOT / "artifacts/philly_idle_rebound_v1/development_eligibility_v2.json"
ARCHIVE_SHA256 = "2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c"
SOURCE = {
    "development": {
        "directory": ROOT / "data/external/msr_philly_2017",
        "audit": "gpu_util_audit.json",
        "sha256": "15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58",
        "bucket": (0, 5),
        "rows": 1_704_704,
    },
    "machine_holdout": {
        "directory": ROOT / "data/external/msr_philly_2017_holdout",
        "audit": "gpu_util_holdout_audit.json",
        "sha256": "146fe4318d6634120bdc10b2af9522be6c0a113c3213fb386a007b4d21edbdf8",
        "bucket": (5, 15),
        "rows": 4_040_392,
    },
}
SPLITS = (
    ("train", 1_507_075_200, 1_510_790_400),  # 2017-10-04 .. 2017-11-16 UTC
    ("validation", 1_510_790_400, 1_512_086_400),
    ("test", 1_512_086_400, 1_513_382_400),
)
GPU_COLUMNS = tuple(f"gpu{i}_util" for i in range(8))
READ_COLUMNS = (
    "machine_id", "time_utc_s", "expected_gpu_slots", "width_matches_first",
    "row_usable", *GPU_COLUMNS,
)
WINDOW_S = 300
DECISION_LAG_S = 600  # latest five-minute window ends at t+300; 300s assumed delivery
HORIZON_S = 7_200
LABEL_DELIVERY_S = 300
HISTORY_WINDOWS = 288  # most recent available windows, including missing-window flags
IDLE_WINDOWS = 6
IDLE_THRESHOLD = 10.0
REBOUND_THRESHOLD = 50.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def machine_bucket(machine_id: str) -> int:
    return int.from_bytes(hashlib.sha256(machine_id.encode("utf-8")).digest(), "big") % 100


def split_at(decision_s: int) -> str | None:
    for name, start_s, end_s in SPLITS:
        if start_s <= decision_s and decision_s + HORIZON_S + LABEL_DELIVERY_S <= end_s:
            return name
    return None


def verify_freeze(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key, path in (("protocol_sha256", PROTOCOL), ("audit_script_sha256", SCRIPT),
                      ("test_script_sha256", TESTS)):
        if manifest.get(key) != sha256_file(path):
            raise ValueError(f"Frozen {key} differs: {path}")
    if manifest.get("source_sha256") != {k: v["sha256"] for k, v in SOURCE.items()}:
        raise ValueError("Frozen source hashes differ")
    return manifest


def _verified_selection_file(relative_path: str, expected_sha256: str, parent: Path) -> Path:
    if not isinstance(relative_path, str) or not relative_path or not isinstance(expected_sha256, str):
        raise ValueError("Selection file path and SHA256 are required")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("Selection file SHA256 must be lowercase hexadecimal")
    candidate = ROOT / relative_path
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"Selection file is missing or a symlink: {relative_path}")
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"Selection file leaves its required directory: {relative_path}") from exc
    if sha256_file(candidate) != expected_sha256:
        raise ValueError(f"Selection file hash differs: {relative_path}")
    return candidate


def verify_selection_for_holdout(selection_path: Path, freeze: dict, freeze_path: Path) -> None:
    if selection_path.is_symlink() or not selection_path.is_file():
        raise ValueError("Holdout selection manifest is missing or a symlink")
    try:
        selection_path.resolve().relative_to((ROOT / "artifacts/philly_idle_rebound_v1").resolve())
    except ValueError as exc:
        raise ValueError("Holdout selection manifest leaves the frozen artifact directory") from exc
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("protocol_sha256") != freeze["protocol_sha256"]:
        raise ValueError("Holdout selection refers to another protocol")
    if (selection.get("freeze_manifest_sha256") != sha256_file(freeze_path)
            or selection.get("development_source_sha256") != SOURCE["development"]["sha256"]
            or selection.get("development_eligibility_sha256") != sha256_file(DEVELOPMENT_ELIGIBILITY)):
        raise ValueError("Holdout selection does not match frozen development evidence")
    if selection.get("selected_model_id") != "l2_logistic":
        raise ValueError("Only the predeclared logistic model can unlock the holdout")
    if selection.get("selected_baseline") not in ("train_beta", "recent_2h_beta", "age_24h_beta"):
        raise ValueError("Holdout baseline was not predeclared")
    model = _verified_selection_file(selection.get("selected_model_path"),
                                     selection.get("selected_model_sha256"),
                                     ROOT / "artifacts/philly_idle_rebound_v1")
    if model.suffix != ".joblib":
        raise ValueError("Selected model must be a frozen joblib file")
    code = _verified_selection_file(selection.get("selection_code_path"),
                                    selection.get("selection_code_sha256"), ROOT / "scripts")
    if code.suffix != ".py":
        raise ValueError("Selection code must be Python source")
    metrics_path = _verified_selection_file(selection.get("validation_metrics_path"),
                                            selection.get("validation_metrics_sha256"),
                                            ROOT / "artifacts/philly_idle_rebound_v1")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if (metrics.get("kind") != "philly_idle_rebound_validation_selection_v1"
            or metrics.get("protocol_sha256") != freeze["protocol_sha256"]
            or metrics.get("development_source_sha256") != SOURCE["development"]["sha256"]
            or metrics.get("development_eligibility_sha256") != selection["development_eligibility_sha256"]
            or metrics.get("selected_model_sha256") != selection["selected_model_sha256"]
            or metrics.get("selection_code_sha256") != selection["selection_code_sha256"]
            or metrics.get("selected_baseline") != selection["selected_baseline"]
            or not isinstance(metrics.get("validation_n"), int)
            or metrics["validation_n"] < 500):
        raise ValueError("Selection manifest and validation metrics disagree")
    baseline_scores = metrics.get("baseline_validation_brier")
    if not isinstance(baseline_scores, dict) or set(baseline_scores) != {
            "train_beta", "recent_2h_beta", "age_24h_beta"}:
        raise ValueError("Validation metrics must contain every frozen baseline")
    if any(not isinstance(v, (int, float)) or not np.isfinite(v) or v < 0
           for v in baseline_scores.values()):
        raise ValueError("Invalid validation Brier")
    order = ("age_24h_beta", "recent_2h_beta", "train_beta")
    chosen = min(order, key=lambda key: (baseline_scores[key], order.index(key)))
    if chosen != selection["selected_baseline"]:
        raise ValueError("Selected baseline does not follow the frozen validation rule")
    logistic_scores = metrics.get("model_validation_brier_by_c")
    if not isinstance(logistic_scores, dict) or set(logistic_scores) != {"0.1", "1", "10"}:
        raise ValueError("Validation metrics must contain every frozen logistic C")
    if any(not isinstance(v, (int, float)) or not np.isfinite(v) or v < 0
           for v in logistic_scores.values()):
        raise ValueError("Invalid logistic validation Brier")
    c_order = ("0.1", "1", "10")
    chosen_c = min(c_order, key=lambda key: (logistic_scores[key], c_order.index(key)))
    if metrics.get("selected_c") != chosen_c or selection.get("selected_c") != chosen_c:
        raise ValueError("Selected logistic C does not follow the frozen validation rule")


def verify_source(cohort: str) -> tuple[Path, dict]:
    spec = SOURCE[cohort]
    directory = spec["directory"]
    path = directory / "gpu_util_normalized_first_width.parquet"
    raw = json.loads((directory / spec["audit"]).read_text(encoding="utf-8"))
    norm = json.loads((directory / "gpu_util_normalized_first_width_audit.json").read_text(encoding="utf-8"))
    if not (raw.get("complete") is True and raw.get("integrity_pass") is True
            and raw.get("compressed_sha256") == ARCHIVE_SHA256
            and norm.get("integrity_pass") is True
            and norm.get("source_archive_sha256") == ARCHIVE_SHA256):
        raise ValueError("Philly archive or normalization lineage did not pass")
    if (norm.get("output_sha256") != spec["sha256"] or norm.get("rows") != spec["rows"]
            or sha256_file(path) != spec["sha256"]):
        raise ValueError("Normalized Parquet differs from the pinned audit")
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows != spec["rows"] or not set(READ_COLUMNS).issubset(parquet.schema_arrow.names):
        raise ValueError("Normalized Parquet rows or schema differ")
    return path, norm


def windows_for_machine(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return aligned 5-minute starts, whole-machine maximum, and validity."""
    if frame.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float), np.array([], dtype=bool)
    slots = frame.expected_gpu_slots.to_numpy(int)
    if len(set(slots)) != 1 or slots[0] not in (2, 4, 8):
        raise ValueError("A machine must have one supported first-observed GPU width")
    n_slots = int(slots[0])
    work = frame.copy()
    work["window_start"] = work.time_utc_s.to_numpy(np.int64) // WINDOW_S * WINDOW_S
    values = work[list(GPU_COLUMNS[:n_slots])].to_numpy(float, na_value=np.nan)
    row_valid = (
        work.row_usable.to_numpy(bool)
        & work.width_matches_first.to_numpy(bool)
        & (work.time_utc_s.to_numpy(np.int64) % 60 == 0)
        & np.all(np.isfinite(values) & (values >= 0) & (values <= 100), axis=1)
    )
    work["valid_minute"] = row_valid
    grouped = work.groupby("window_start", sort=True)
    quality = grouped.agg(
        n=("time_utc_s", "size"), unique_minutes=("time_utc_s", "nunique"),
        first=("time_utc_s", "min"), last=("time_utc_s", "max"),
        all_valid=("valid_minute", "all"),
    )
    starts = quality.index.to_numpy(np.int64)
    observed_ok = (
        quality.n.to_numpy(int) == 5
    ) & (quality.unique_minutes.to_numpy(int) == 5) & (
        quality["first"].to_numpy(np.int64) == starts
    ) & (
        quality["last"].to_numpy(np.int64) == starts + 240
    ) & quality.all_valid.to_numpy(bool)
    means = grouped[list(GPU_COLUMNS[:n_slots])].mean().to_numpy(float)
    peak = np.max(means, axis=1)
    grid = np.arange(int(starts.min()), int(starts.max()) + WINDOW_S, WINDOW_S, dtype=np.int64)
    offset = (starts - grid[0]) // WINDOW_S
    values_grid = np.full(len(grid), np.nan, dtype=float)
    valid_grid = np.zeros(len(grid), dtype=bool)
    values_grid[offset] = peak
    valid_grid[offset] = observed_ok & np.isfinite(peak)
    return grid, values_grid, valid_grid


def iter_decisions(machine_id: str, grid: np.ndarray, peak: np.ndarray, valid: np.ndarray,
                   *, allowed_splits: frozenset[str] = frozenset(("train", "validation", "test"))):
    """Yield only idle-input candidates; label is None when future is unobserved."""
    if not (len(grid) == len(peak) == len(valid)):
        raise ValueError("Window arrays must align")
    for i in range(IDLE_WINDOWS - 1, len(grid)):
        decision = int(grid[i]) + DECISION_LAG_S
        if decision % 1_800 != 0:
            continue
        split = split_at(decision)
        if split not in allowed_splits:
            continue
        recent = slice(i - IDLE_WINDOWS + 1, i + 1)
        if not valid[recent].all() or not np.all(peak[recent] < IDLE_THRESHOLD):
            continue
        future = slice(i + 2, i + 26)  # [d,d+7200), never the latency-delayed current window
        future_valid = valid[future]
        future_complete = bool(len(future_valid) == 24 and future_valid.all())
        history = valid[max(0, i - HISTORY_WINDOWS + 1):i + 1]
        yield {
            "split": split,
            "machine_id": machine_id,
            "decision_utc_s": decision,
            "history_valid_count": int(history.sum()),
            "history_complete": bool(len(history) == HISTORY_WINDOWS and history.all()),
            "future_complete": future_complete,
            "rebound": bool(np.any(peak[future] >= REBOUND_THRESHOLD)) if future_complete else None,
        }


def summarize_decisions(decisions: list[dict], *, cohort: str, source_sha256: str) -> dict:
    result = {"kind": "eligibility_only_no_model_no_row_labels", "cohort": cohort,
              "source_sha256": source_sha256, "splits": {}, "quality_gate": {}}
    split_names = ("test",) if cohort == "machine_holdout" else tuple(x[0] for x in SPLITS)
    for name in split_names:
        rows = [r for r in decisions if r["split"] == name]
        observed = [r for r in rows if r["future_complete"]]
        positive = [r for r in observed if r["rebound"]]
        negative = [r for r in observed if not r["rebound"]]
        positive_machine_days = {(r["machine_id"], r["decision_utc_s"] // 86_400) for r in positive}
        machine_days: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for row in rows:
            machine_days[(row["machine_id"], row["decision_utc_s"] // 86_400)].append(row)
        supported_machine_day_rates = [
            sum(row["future_complete"] for row in cell) / len(cell)
            for cell in machine_days.values() if len(cell) >= 4
        ]
        complete_history_mean = (
            sum(r["history_valid_count"] for r in observed) / (HISTORY_WINDOWS * len(observed))
            if observed else None
        )
        unknown = [r for r in rows if not r["future_complete"]]
        unknown_history_mean = (
            sum(r["history_valid_count"] for r in unknown) / (HISTORY_WINDOWS * len(unknown))
            if unknown else None
        )
        history_gap = (
            abs(complete_history_mean - unknown_history_mean)
            if complete_history_mean is not None and unknown_history_mean is not None else None
        )
        episodes = 0
        for machine in sorted({r["machine_id"] for r in positive}):
            stamps = sorted(r["decision_utc_s"] for r in positive if r["machine_id"] == machine)
            episodes += 1 + sum(b - a > HORIZON_S for a, b in zip(stamps, stamps[1:]))
        result["splits"][name] = {
            "idle_input_candidates": len(rows),
            "future_complete": len(observed),
            "future_observation_fraction": len(observed) / len(rows) if rows else 0.0,
            "positive_rebound": len(positive), "negative_rebound": len(negative),
            "eligible_machines": len({r["machine_id"] for r in observed}),
            "positive_machines": len({r["machine_id"] for r in positive}),
            "negative_machines": len({r["machine_id"] for r in negative}),
            "positive_utc_days": len({r["decision_utc_s"] // 86_400 for r in positive}),
            "positive_machine_days": len(positive_machine_days),
            "positive_runs_gap_over_2h": episodes,
            "machine_days_with_at_least_4_candidates": len(supported_machine_day_rates),
            "machine_days_future_observed_below_50pct": sum(x < 0.5 for x in supported_machine_day_rates),
            "machine_day_future_observed_p10": (
                float(np.quantile(supported_machine_day_rates, 0.1))
                if supported_machine_day_rates else None
            ),
            "history_24h_valid_fraction_complete_mean": complete_history_mean,
            "history_24h_valid_fraction_unknown_mean": unknown_history_mean,
            "history_24h_complete_unknown_gap": history_gap,
            "history_24h_complete": sum(r["history_complete"] for r in observed),
            "history_24h_partial": sum(not r["history_complete"] for r in observed),
            "history_24h_valid_window_min": min((r["history_valid_count"] for r in observed), default=None),
        }
        q = result["splits"][name]
        result["quality_gate"][name] = {
            "pass": bool(q["future_observation_fraction"] >= 0.9
                         and q["future_complete"] >= 500
                         and q["positive_rebound"] >= 100
                         and q["negative_rebound"] >= 100
                         and q["eligible_machines"] >= 10
                         and q["positive_machines"] >= 5
                         and q["positive_utc_days"] >= 5
                         and q["positive_runs_gap_over_2h"] >= 20
                         and q["machine_days_with_at_least_4_candidates"] >= 10
                         and q["machine_days_future_observed_below_50pct"]
                             / q["machine_days_with_at_least_4_candidates"] <= 0.1
                         and (len(unknown) < 20 or history_gap is not None and history_gap <= 0.2)),
        }
    result["quality_gate"]["all_pass"] = all(v["pass"] for k, v in result["quality_gate"].items() if k != "all_pass")
    return result


def audit(cohort: str, freeze_path: Path, selection_path: Path | None = None) -> dict:
    freeze = verify_freeze(freeze_path)
    if cohort == "machine_holdout":
        if selection_path is None:
            raise ValueError("Machine holdout stays locked until model/baseline selection is frozen")
        verify_selection_for_holdout(selection_path, freeze, freeze_path)
    path, normalized_audit = verify_source(cohort)
    frame = pd.read_parquet(path, columns=list(READ_COLUMNS))
    if len(frame) != SOURCE[cohort]["rows"]:
        raise ValueError("Loaded source row count changed")
    machine_ids = set(frame.machine_id.astype(str).unique())
    lo, hi = SOURCE[cohort]["bucket"]
    if any(not lo <= machine_bucket(machine) < hi for machine in machine_ids):
        raise ValueError("Machine ID leaves its preselected hash bucket")
    if machine_ids != set(normalized_audit["machines"]):
        raise ValueError("Machine inventory differs from normalized audit")
    decisions: list[dict] = []
    allowed_splits = frozenset(("test",)) if cohort == "machine_holdout" else frozenset(x[0] for x in SPLITS)
    for machine_id, group in frame.groupby("machine_id", sort=True):
        grid, peak, valid = windows_for_machine(group)
        decisions.extend(iter_decisions(str(machine_id), grid, peak, valid,
                                        allowed_splits=allowed_splits))
    result = summarize_decisions(decisions, cohort=cohort, source_sha256=SOURCE[cohort]["sha256"])
    result["freeze_manifest_sha256"] = sha256_file(freeze_path)
    result["protocol_sha256"] = freeze["protocol_sha256"]
    result["audit_script_sha256"] = freeze["audit_script_sha256"]
    if selection_path is not None:
        result["selection_manifest_sha256"] = sha256_file(selection_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=tuple(SOURCE), default="development")
    parser.add_argument("--freeze-manifest", type=Path, default=FREEZE)
    parser.add_argument("--selection-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.cohort, args.freeze_manifest, args.selection_manifest)
    output = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise FileExistsError(f"Refuse to overwrite eligibility result: {args.output}")
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
