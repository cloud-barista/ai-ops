"""Score frozen Philly v3 on development validation only, after explicit hash lock.

Never reads December or the disjoint-machine cohort. Run only after an
independent review of validation_score_authorization.json and pass its SHA256
through --expected-authorization-sha256. Importing this module never scores.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_philly_idle_rebound as original
import audit_philly_idle_rebound_adaptive_v3 as support_audit
import philly_idle_rebound_adaptive_v3 as method


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_evaluate_philly_idle_rebound_adaptive_v3_validation.py"
NOTE = ROOT / "reports/philly_idle_rebound_adaptive_v3_scoring_note.md"
AUTHORIZATION = ROOT / "artifacts/philly_idle_rebound_adaptive_v3/validation_score_authorization.json"
OUTPUT = ROOT / "artifacts/philly_idle_rebound_adaptive_v3"
METRICS = OUTPUT / "validation_metrics.json"
PREDICTIONS = OUTPUT / "validation_predictions.parquet"
EXPECTED_FREEZE_SHA = "d0d0f24915773d0f7536d2737c9fabe65d1d3b18d90999d8e8a2c6c8815a55d4"
EXPECTED_SUPPORT_SHA = "1eddb9f400c7aefa0a5260743a71cd3a672cd24c3377ef36331664f17c96f7fa"
EXPECTED_ORIGINAL_AUDIT_SHA = "a2597040751c9723632a0cabc9e494c53697b07ba066215be321e61e846bb599"
EXPECTED_SOURCE_SHA = "15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58"
EXPECTED_FROZEN_BRIER = 0.046015034063580054
EXPECTED_VALIDATION_N = 4_839
EXPECTED_VALIDATION_POSITIVE = 229
HALVES = (("nov16_to_nov23", 1_510_790_400, 1_511_395_200),
          ("nov23_to_dec01", 1_511_395_200, 1_512_086_400))
BOOTSTRAP_DRAWS = 2_000
BOOTSTRAP_SEED = 43


def _is_lower_sha(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def verify_authorization(expected_sha256: str, path: Path = AUTHORIZATION) -> tuple[dict, dict]:
    """The reviewer's exact authorization-file digest is mandatory."""
    if not _is_lower_sha(expected_sha256):
        raise ValueError("Expected authorization SHA256 must be 64 lowercase hex digits")
    if path.is_symlink() or not path.is_file() or original.sha256_file(path) != expected_sha256:
        raise ValueError("Validation authorization file is absent or differs from reviewed SHA")
    auth = json.loads(path.read_text(encoding="utf-8"))
    if (auth.get("kind") != "khu.philly_idle_rebound_adaptive_v3.validation_score_authorization"
            or auth.get("status") != "validation_only_pre_score_frozen"):
        raise ValueError("Wrong validation score authorization kind or status")
    pinned = {
        "score_free_freeze_sha256": (support_audit.FREEZE, EXPECTED_FREEZE_SHA),
        "score_free_support_sha256": (support_audit.OUTPUT, EXPECTED_SUPPORT_SHA),
        "original_audit_sha256": (original.SCRIPT, EXPECTED_ORIGINAL_AUDIT_SHA),
        "development_source_sha256": (
            original.SOURCE["development"]["directory"]
            / "gpu_util_normalized_first_width.parquet", EXPECTED_SOURCE_SHA),
    }
    for key, (file, expected) in pinned.items():
        if auth.get(key) != expected or original.sha256_file(file) != expected:
            raise ValueError(f"Pinned {key} differs")
    for key, file in (("scorer_sha256", SCRIPT), ("test_sha256", TESTS),
                      ("scoring_note_sha256", NOTE)):
        if auth.get(key) != original.sha256_file(file):
            raise ValueError(f"Frozen {key} differs")
    versions = {"python": platform.python_version(), "numpy": np.__version__,
                "pandas": pd.__version__, "pyarrow": pyarrow.__version__}
    if auth.get("versions") != versions:
        raise ValueError("Scorer environment differs from pinned versions")
    support_audit.verify_freeze(support_audit.FREEZE)
    support = json.loads(support_audit.OUTPUT.read_text(encoding="utf-8"))
    if (support.get("kind") != "philly_idle_rebound_adaptive_v3_score_free_support"
            or support.get("score_free") is not True
            or support.get("cohort") != "development_validation_only"
            or support.get("freeze_manifest_sha256") != EXPECTED_FREEZE_SHA
            or support.get("development_source_sha256") != EXPECTED_SOURCE_SHA
            or support.get("support_gate", {}).get("all_pass") is not True
            or support.get("support_gate", {}).get("fleet_at_least_50pct") is not True
            or support.get("support_gate", {}).get("fleet_and_local_at_least_10pct") is not True
            or support.get("validation_input_candidates") != EXPECTED_VALIDATION_N
            or support.get("anchors_selected_from_input_only") != 1_240
            or support.get("anchors_complete") != 1_240):
        raise ValueError("Score-free validation support is not the approved passed audit")
    return auth, support


def read_validation(source: Path) -> pd.DataFrame:
    frame = pd.read_parquet(source, columns=list(original.READ_COLUMNS),
                            filters=[("time_utc_s", "<", support_audit.VALIDATION_END_S)])
    if frame.empty or int(frame.time_utc_s.max()) >= support_audit.VALIDATION_END_S:
        raise ValueError("Validation reader crossed December boundary")
    return frame


def build_validation(frame: pd.DataFrame, source_audit: dict,
                     expected_support: dict) -> tuple[list[method.CandidateKey],
                                                      list[method.AnchorOutcome],
                                                      dict[method.CandidateKey, int],
                                                      frozenset[str]]:
    """Choose anchors before reading any candidate's future label."""
    keys: list[method.CandidateKey] = []
    references: dict[method.CandidateKey, tuple[str, int]] = {}
    windows_by_machine: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for machine_id, group in frame.groupby("machine_id", sort=True):
        machine = str(machine_id)
        windows = original.windows_for_machine(group)
        windows_by_machine[machine] = windows
        for key, index in support_audit.input_candidates(machine, *windows):
            if key in references:
                raise ValueError("Duplicate validation machine/decision key")
            references[key] = (machine, index)
            keys.append(key)
    fleet_ids = support_audit.verified_development_fleet_ids(
        frozenset(windows_by_machine), source_audit)
    anchor_keys = method.select_input_anchors(keys)
    anchors = []
    for key in anchor_keys:
        machine, index = references[key]
        anchors.append(method.AnchorOutcome(machine, key.decision_utc_s,
                                             support_audit.label_anchor(*windows_by_machine[machine],
                                                                        index)))
    summary = support_audit.summarize_support(keys, anchors, fleet_ids)
    if any(summary.get(key) != expected_support.get(key) for key in summary):
        raise ValueError("Recomputed validation anchors/support differ from frozen score-free audit")
    if any(r.rebound is None for r in anchors):
        raise ValueError("All approved validation anchors must have complete labels")
    labels: dict[method.CandidateKey, int] = {}
    for key in keys:
        machine, index = references[key]
        label = support_audit.label_anchor(*windows_by_machine[machine], index)
        if label is None:
            raise ValueError("Validation candidate has an incomplete future label")
        labels[key] = label
    if (len(keys) != EXPECTED_VALIDATION_N or len(labels) != len(keys)
            or sum(labels.values()) != EXPECTED_VALIDATION_POSITIVE):
        raise ValueError("Validation label count differs from frozen eligibility/selection")
    return keys, anchors, labels, fleet_ids


def prediction_frame(keys: list[method.CandidateKey],
                     anchors: list[method.AnchorOutcome],
                     labels: dict[method.CandidateKey, int],
                     fleet_ids: frozenset[str]) -> pd.DataFrame:
    rows = []
    for key in sorted(keys, key=lambda item: (item.decision_utc_s, item.machine_id)):
        probabilities = method.probabilities(
            anchors, key.machine_id, key.decision_utc_s,
            fleet_source_machine_ids=fleet_ids)
        rows.append({"machine_id": key.machine_id,
                     "decision_utc_s": key.decision_utc_s,
                     "utc_day": key.decision_utc_s // 86_400,
                     "y": labels[key], **probabilities})
    return pd.DataFrame(rows)


def validate_predictions(frame: pd.DataFrame, *, expected_rows: int | None = None) -> None:
    columns = {"machine_id", "decision_utc_s", "utc_day", "y", "candidate",
               *method.BASELINE_TIE_ORDER}
    if not columns.issubset(frame.columns) or frame.empty:
        raise ValueError("Validation prediction columns are missing")
    if expected_rows is not None and len(frame) != expected_rows:
        raise ValueError("Validation prediction row count differs")
    if frame[["machine_id", "decision_utc_s"]].duplicated().any():
        raise ValueError("Duplicate prediction machine/decision key")
    if frame.machine_id.isna().any() or not frame.machine_id.map(
            lambda x: isinstance(x, str) and bool(x)).all():
        raise ValueError("Machine ID must be a nonempty string")
    for name in ("decision_utc_s", "utc_day", "y"):
        if frame[name].isna().any() or not np.isfinite(frame[name].to_numpy(float)).all():
            raise ValueError(f"Nonfinite {name}")
    if not np.isin(frame.y.to_numpy(), [0, 1]).all():
        raise ValueError("Only observed binary validation labels are allowed")
    raw_decision = frame.decision_utc_s.to_numpy(float)
    raw_day = frame.utc_day.to_numpy(float)
    if (not np.array_equal(raw_decision, raw_decision.astype(np.int64))
            or not np.array_equal(raw_day, raw_day.astype(np.int64))):
        raise ValueError("Decision time and UTC day must be integers")
    decision = frame.decision_utc_s.to_numpy(np.int64)
    if (not np.array_equal(frame.utc_day.to_numpy(np.int64), decision // 86_400)
            or np.any(decision % 1_800)
            or np.any(decision < method.VALIDATION_START_S)
            or np.any(decision >= support_audit.VALIDATION_END_S)):
        raise ValueError("Validation decision time/day is invalid")
    for name in ("candidate", *method.BASELINE_TIE_ORDER):
        p = frame[name].to_numpy(float)
        if not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
            raise ValueError(f"Invalid probability in {name}")


def brier(y: np.ndarray, p: np.ndarray) -> float:
    if len(y) == 0 or len(y) != len(p):
        raise ValueError("Brier needs equal nonempty arrays")
    if not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ValueError("Brier received nonfinite values")
    return float(np.mean((p - y) ** 2))


def cluster_bootstrap(frame: pd.DataFrame, baseline: str, group: str,
                      *, draws: int = BOOTSTRAP_DRAWS,
                      seed: int = BOOTSTRAP_SEED) -> dict:
    if group not in ("machine_id", "utc_day") or draws <= 0:
        raise ValueError("Bootstrap group/draws are invalid")
    y = frame.y.to_numpy(float)
    delta = (frame.candidate.to_numpy(float) - y) ** 2 - (
        frame[baseline].to_numpy(float) - y) ** 2
    grouped = pd.DataFrame({"group": frame[group].to_numpy(), "delta": delta}).groupby(
        "group", sort=True).delta.agg(["sum", "count"])
    if len(grouped) < 2:
        raise ValueError("Paired bootstrap requires at least two clusters")
    sums = grouped["sum"].to_numpy(float)
    counts = grouped["count"].to_numpy(float)
    rng = np.random.default_rng(seed)
    estimates = np.empty(draws, dtype=float)
    for k in range(draws):
        sampled = rng.integers(0, len(grouped), size=len(grouped))
        estimates[k] = sums[sampled].sum() / counts[sampled].sum()
    return {"group": group, "clusters": len(grouped), "draws": draws, "seed": seed,
            "point_delta": float(delta.mean()),
            "percentile_95": [float(x) for x in np.quantile(estimates, [0.025, 0.975])]}


def low_risk_twenty_percent(frame: pd.DataFrame, probability: str) -> dict:
    work = frame[["machine_id", "decision_utc_s", "y", probability]].copy()
    work["tie_hash"] = [method.low_risk_tie_key(str(m), int(d))
                        for m, d in zip(work.machine_id, work.decision_utc_s)]
    chosen = work.sort_values([probability, "tie_hash", "machine_id", "decision_utc_s"],
                              kind="mergesort").iloc[:math.ceil(0.2 * len(work))]
    if chosen.empty:
        raise ValueError("Low-risk selection is empty")
    return {"rows": len(chosen), "positive": int(chosen.y.sum()),
            "event_rate": float(chosen.y.mean())}


def score_predictions(frame: pd.DataFrame, *, draws: int = BOOTSTRAP_DRAWS) -> dict:
    """Pure score function for synthetic tests; production calls draws=2000."""
    validate_predictions(frame)
    y = frame.y.to_numpy(float)
    names = ("candidate", *method.BASELINE_TIE_ORDER)
    row_brier = {name: brier(y, frame[name].to_numpy(float)) for name in names}
    simple = {name: row_brier[name] for name in method.BASELINE_TIE_ORDER}
    best = method.best_simple_baseline(simple)
    adaptive = tuple(name for name in method.BASELINE_TIE_ORDER if name != "frozen_train_beta")
    best_adaptive_brier = min(row_brier[name] for name in adaptive)

    machine_brier = {}
    for name in names:
        per_machine = frame.groupby("machine_id", sort=True).apply(
            lambda cell: brier(cell.y.to_numpy(float), cell[name].to_numpy(float)),
            include_groups=False)
        machine_brier[name] = float(per_machine.mean())
    best_simple_macro = min(machine_brier[name] for name in method.BASELINE_TIE_ORDER)

    halves = []
    for label, start, end in HALVES:
        part = frame[(frame.decision_utc_s >= start) & (frame.decision_utc_s < end)]
        if part.empty:
            raise ValueError(f"Fixed validation half is empty: {label}")
        yy = part.y.to_numpy(float)
        scores = {name: brier(yy, part[name].to_numpy(float)) for name in names}
        best_simple_half = min(scores[name] for name in method.BASELINE_TIE_ORDER)
        halves.append({"name": label, "rows": len(part), "positive": int(yy.sum()),
                       "brier": scores, "best_simple_brier": best_simple_half,
                       "candidate_no_worse": scores["candidate"] <= best_simple_half})
    if sum(row["rows"] for row in halves) != len(frame):
        raise ValueError("Fixed validation halves do not cover all rows")

    boot = {group: cluster_bootstrap(frame, best, group, draws=draws)
            for group in ("machine_id", "utc_day")}
    low_risk = {name: low_risk_twenty_percent(frame, name)
                for name in ("candidate", best)}
    if low_risk["candidate"]["rows"] != low_risk[best]["rows"]:
        raise AssertionError("Low-risk selectors used different row counts")
    checks = {
        "candidate_5pct_better_than_frozen_train_beta": (
            row_brier["candidate"] <= 0.95 * row_brier["frozen_train_beta"]),
        "candidate_2pct_better_than_every_adaptive_baseline": (
            row_brier["candidate"] <= 0.98 * best_adaptive_brier),
        "machine_equal_no_worse_than_best_of_six": (
            machine_brier["candidate"] <= best_simple_macro),
        "both_halves_no_worse_than_each_half_best_of_six": all(
            part["candidate_no_worse"] for part in halves),
        "machine_bootstrap_upper_below_zero": boot["machine_id"]["percentile_95"][1] < 0,
        "utc_day_bootstrap_upper_below_zero": boot["utc_day"]["percentile_95"][1] < 0,
        "low_risk_20pct_event_rate_no_higher": (
            low_risk["candidate"]["event_rate"] <= low_risk[best]["event_rate"]),
    }
    return {"rows": len(frame), "positive": int(y.sum()),
            "row_brier": row_brier, "machine_equal_brier": machine_brier,
            "best_full_validation_simple_baseline": best,
            "best_adaptive_baseline_brier": best_adaptive_brier,
            "best_machine_equal_simple_brier": best_simple_macro,
            "halves": halves, "paired_bootstrap": boot,
            "low_risk_20pct": low_risk,
            "acceptance": {**checks, "all_pass": all(checks.values())}}


def run(expected_authorization_sha256: str) -> dict:
    if METRICS.exists() or PREDICTIONS.exists():
        raise FileExistsError("Refuse to overwrite any validation score artifact")
    auth, expected_support = verify_authorization(expected_authorization_sha256)
    source, norm_audit = original.verify_source("development")
    keys, anchors, labels, fleet_ids = build_validation(
        read_validation(source), norm_audit, expected_support)
    frame = prediction_frame(keys, anchors, labels, fleet_ids)
    validate_predictions(frame, expected_rows=EXPECTED_VALIDATION_N)
    metrics = score_predictions(frame, draws=BOOTSTRAP_DRAWS)
    if (metrics["positive"] != EXPECTED_VALIDATION_POSITIVE
            or abs(metrics["row_brier"]["frozen_train_beta"] - EXPECTED_FROZEN_BRIER) > 1e-12):
        raise ValueError("Frozen reference Brier/label count could not be reproduced")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".philly_idle_v3_validation_", dir=OUTPUT) as name:
        temporary = Path(name)
        pred_tmp = temporary / PREDICTIONS.name
        metrics_tmp = temporary / METRICS.name
        frame.to_parquet(pred_tmp, index=False)
        metrics.update({
            "kind": "philly_idle_rebound_adaptive_v3_exploratory_validation",
            "authorization_sha256": expected_authorization_sha256,
            "score_free_freeze_sha256": EXPECTED_FREEZE_SHA,
            "score_free_support_sha256": EXPECTED_SUPPORT_SHA,
            "development_source_sha256": EXPECTED_SOURCE_SHA,
            "scorer_sha256": auth["scorer_sha256"],
            "test_sha256": auth["test_sha256"],
            "predictions_sha256": original.sha256_file(pred_tmp),
            "note": "Development validation is exploratory; no December or other-machine idle-rebound label was read.",
        })
        metrics_tmp.write_text(json.dumps(metrics, sort_keys=True, indent=2,
                                          allow_nan=False) + "\n", encoding="utf-8")
        if METRICS.exists() or PREDICTIONS.exists():
            raise FileExistsError("Validation score output appeared during scoring")
        pred_tmp.replace(PREDICTIONS)
        metrics_tmp.replace(METRICS)
    return {"metrics": str(METRICS), "predictions": str(PREDICTIONS),
            "acceptance": metrics["acceptance"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-authorization-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.expected_authorization_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
