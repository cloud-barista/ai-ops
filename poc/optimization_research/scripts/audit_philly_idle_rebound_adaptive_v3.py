"""Score-free v3 Philly idle-rebound anchor and causal-support audit.

Only the original development source before December is read. Labels are
computed for input-chosen validation anchors solely to count mature support;
there are no probabilities, Brier scores, row labels, or holdout accesses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_philly_idle_rebound as original
import philly_idle_rebound_adaptive_v3 as method


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/philly_idle_rebound_adaptive_v3_protocol.md"
METHOD = ROOT / "scripts/philly_idle_rebound_adaptive_v3.py"
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_philly_idle_rebound_adaptive_v3.py"
FREEZE = ROOT / "artifacts/philly_idle_rebound_adaptive_v3/freeze_manifest.json"
OUTPUT = ROOT / "artifacts/philly_idle_rebound_adaptive_v3/score_free_support.json"
V2_ELIGIBILITY = ROOT / "artifacts/philly_idle_rebound_v1/development_eligibility_v2.json"
V2_VALIDATION = ROOT / "artifacts/philly_idle_rebound_v1/validation_metrics_v2.json"
VALIDATION_END_S = original.SPLITS[1][2]


def verify_freeze(path: Path = FREEZE) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("v3 freeze manifest is absent or is a symlink")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    if (frozen.get("kind") != "khu.philly_idle_rebound_adaptive_v3.freeze"
            or frozen.get("status") != "score_free_support_only"):
        raise ValueError("Wrong v3 freeze kind or status")
    expected = {
        "protocol_sha256": PROTOCOL,
        "method_sha256": METHOD,
        "audit_sha256": SCRIPT,
        "test_sha256": TESTS,
        "v2_eligibility_sha256": V2_ELIGIBILITY,
        "v2_validation_sha256": V2_VALIDATION,
    }
    for key, file in expected.items():
        if frozen.get(key) != original.sha256_file(file):
            raise ValueError(f"Frozen {key} differs")
    if frozen.get("development_source_sha256") != original.SOURCE["development"]["sha256"]:
        raise ValueError("Frozen development source differs")
    eligible = json.loads(V2_ELIGIBILITY.read_text(encoding="utf-8"))
    v2 = json.loads(V2_VALIDATION.read_text(encoding="utf-8"))
    if (eligible.get("quality_gate", {}).get("all_pass") is not True
            or eligible.get("source_sha256") != frozen["development_source_sha256"]
            or eligible.get("splits", {}).get("validation", {}).get("idle_input_candidates") != 4_839
            or eligible.get("splits", {}).get("validation", {}).get("future_complete") != 4_839
            or v2.get("development_source_sha256") != frozen["development_source_sha256"]
            or v2.get("development_eligibility_sha256") != frozen["v2_eligibility_sha256"]
            or v2.get("train_n") != 6_345 or v2.get("train_positive") != 493
            or v2.get("validation_n") != 4_839 or v2.get("validation_positive") != 229
            or method.P0 != (v2["train_positive"] + 1) / (v2["train_n"] + 2)):
        raise ValueError("Original development support no longer matches v3 prior/cohort")
    return frozen


def verified_development_fleet_ids(machine_ids: frozenset[str], norm_audit: dict
                                   ) -> frozenset[str]:
    """Bind the scorer's explicit fleet allow-list to the pinned <5 source."""
    recorded = norm_audit.get("machines")
    if (not isinstance(recorded, dict) or not machine_ids
            or machine_ids != frozenset(map(str, recorded))
            or any(not 0 <= original.machine_bucket(machine) < 5 for machine in machine_ids)):
        raise ValueError("Fleet IDs differ from the verified original <5 machine cohort")
    return machine_ids


def input_candidates(machine_id: str, grid: np.ndarray, peak: np.ndarray,
                     valid: np.ndarray):
    """Yield (key, grid index) using only current and earlier GPU windows."""
    if not (len(grid) == len(peak) == len(valid)):
        raise ValueError("Window arrays must align")
    for i in range(original.IDLE_WINDOWS - 1, len(grid)):
        d = int(grid[i]) + original.DECISION_LAG_S
        if d % 1_800 or not (method.VALIDATION_START_S <= d
                             and d + method.LABEL_MATURITY_S <= VALIDATION_END_S):
            continue
        recent = slice(i - original.IDLE_WINDOWS + 1, i + 1)
        if valid[recent].all() and np.all(peak[recent] < original.IDLE_THRESHOLD):
            yield method.CandidateKey(machine_id, d), i


def label_anchor(grid: np.ndarray, peak: np.ndarray, valid: np.ndarray,
                 index: int) -> int | None:
    """Called only AFTER anchors have been chosen without this future data."""
    future = slice(index + 2, index + 26)
    future_valid = valid[future]
    if len(future_valid) != 24 or not future_valid.all():
        return None
    return int(np.any(peak[future] >= original.REBOUND_THRESHOLD))


def summarize_support(candidates: list[method.CandidateKey],
                      anchors: list[method.AnchorOutcome],
                      fleet_source_machine_ids: frozenset[str]) -> dict:
    """Aggregate counts only; no candidate probability or individual outcome."""
    ordered = sorted(candidates, key=lambda row: (row.decision_utc_s, row.machine_id))
    fleet_supported = local_and_fleet_supported = 0
    # Complete and unknown anchors are both retained; only complete, matured
    # outcomes are admitted to each decision's support history.
    for row in ordered:
        fleet, local = method.support_for_decision(anchors, row.machine_id,
                                                   row.decision_utc_s,
                                                   fleet_source_machine_ids=fleet_source_machine_ids)
        if fleet.fleet_ok:
            fleet_supported += 1
            if local.local_ok:
                local_and_fleet_supported += 1
    n = len(ordered)
    observed = [r for r in anchors if r.rebound is not None]
    fleet_gate = n > 0 and 2 * fleet_supported >= n
    local_gate = n > 0 and 10 * local_and_fleet_supported >= n
    return {
        "validation_input_candidates": n,
        "validation_candidate_machines": len({r.machine_id for r in ordered}),
        "anchors_selected_from_input_only": len(anchors),
        "anchors_complete": len(observed),
        "anchors_unknown": len(anchors) - len(observed),
        "anchors_cumulative_positive": sum(r.rebound == 1 for r in observed),
        "anchors_cumulative_negative": sum(r.rebound == 0 for r in observed),
        "fleet_supported_candidates": fleet_supported,
        "fleet_support_fraction": fleet_supported / n if n else 0.0,
        "fleet_and_local_supported_candidates": local_and_fleet_supported,
        "fleet_and_local_support_fraction": local_and_fleet_supported / n if n else 0.0,
        "support_gate": {
            "fleet_at_least_50pct": fleet_gate,
            "fleet_and_local_at_least_10pct": local_gate,
            "all_pass": fleet_gate and local_gate,
        },
    }


def audit(path: Path = FREEZE) -> dict:
    freeze = verify_freeze(path)
    source, norm_audit = original.verify_source("development")
    frame = pd.read_parquet(source, columns=list(original.READ_COLUMNS),
                            filters=[("time_utc_s", "<", VALIDATION_END_S)])
    if frame.empty or int(frame.time_utc_s.max()) >= VALIDATION_END_S:
        raise ValueError("v3 support reader crossed the December boundary")
    all_candidates: list[method.CandidateKey] = []
    references: dict[method.CandidateKey, tuple[str, int]] = {}
    machine_windows: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for machine_id, group in frame.groupby("machine_id", sort=True):
        machine = str(machine_id)
        windows = original.windows_for_machine(group)
        machine_windows[machine] = windows
        for key, index in input_candidates(machine, *windows):
            if key in references:
                raise ValueError("Duplicate validation input candidate")
            all_candidates.append(key)
            references[key] = (machine, index)
    anchors = []
    for key in method.select_input_anchors(all_candidates):
        machine, index = references[key]
        windows = machine_windows[machine]
        anchors.append(method.AnchorOutcome(machine, key.decision_utc_s,
                                             label_anchor(*windows, index)))
    fleet_ids = verified_development_fleet_ids(frozenset(machine_windows), norm_audit)
    summary = summarize_support(all_candidates, anchors, fleet_ids)
    if summary["validation_input_candidates"] != 4_839:
        raise ValueError("Input candidate count differs from original validation audit")
    if summary["anchors_complete"] + summary["anchors_unknown"] != summary["anchors_selected_from_input_only"]:
        raise AssertionError("Anchor completeness accounting differs")
    return {
        "kind": "philly_idle_rebound_adaptive_v3_score_free_support",
        "score_free": True,
        "cohort": "development_validation_only",
        "freeze_manifest_sha256": original.sha256_file(path),
        "protocol_sha256": freeze["protocol_sha256"],
        "development_source_sha256": freeze["development_source_sha256"],
        "v2_eligibility_sha256": freeze["v2_eligibility_sha256"],
        "v2_validation_sha256": freeze["v2_validation_sha256"],
        **summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=FREEZE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refuse to overwrite score-free support: {args.output}")
    result = audit(args.freeze)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "support_gate": result["support_gate"]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
