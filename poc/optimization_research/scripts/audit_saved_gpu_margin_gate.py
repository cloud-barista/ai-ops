"""Posthoc abstention audit on pinned saved GPU-shape decision rows.

The thresholds here are diagnostics selected after earlier outcomes were
published. They must not be treated as validated deployment policies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/saved_gpu_shape_decision_audit_v1/decision_rows.csv"
SOURCE_SHA256 = "2d7e1cb291900d45dad77ded0e0702e9a9f570fc28e695273cecb7bbce898325"
OUTPUT = ROOT / "artifacts/saved_gpu_margin_gate_v1/results.json"
THRESHOLDS = (0.0, 0.1, 0.2)
EXPECTED_ORIGINAL = {
    "khu-hf-local-exhaustive": (90, 54, 54, 0),
    "khu-hf-local-novel-matrix": (10, 9, 9, 0),
    "khu-hf-local-space-filling": (33, 25, 25, 0),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def truth(value: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"unexpected Boolean: {value!r}")
    return value == "True"


def analyze(rows: list[dict[str, str]]) -> dict:
    required = {
        "campaign", "workload_group", "model_id", "method", "scenario", "selected",
        "true_success", "false_approval", "minimum_predicted_relative_margin",
    }
    selected = []
    seen = set()
    counts = defaultdict(int)
    for row in rows:
        if not required <= row.keys():
            raise ValueError(f"missing decision columns: {sorted(required - row.keys())}")
        if row["method"] != "stored_model":
            continue
        key = (row["campaign"], row["workload_group"], row["scenario"])
        if key in seen:
            raise ValueError(f"duplicate stored decision: {key}")
        seen.add(key)
        group = (row["campaign"], row["scenario"])
        counts[group] += 1
        chosen = truth(row["selected"])
        success = truth(row["true_success"])
        false = truth(row["false_approval"])
        if success and false or (success or false) != chosen:
            raise ValueError(f"inconsistent decision outcome: {key}")
        if chosen:
            margin = float(row["minimum_predicted_relative_margin"])
            if not math.isfinite(margin) or margin < 0:
                raise ValueError(f"invalid selected margin: {key}")
            selected.append((row["campaign"], row["scenario"], row["workload_group"],
                             row["model_id"], margin, success, false))
        elif row["minimum_predicted_relative_margin"]:
            raise ValueError(f"unselected decision has margin: {key}")

    original = {}
    for campaign, (expected_n, expected_selected, expected_success, expected_false) in EXPECTED_ORIGINAL.items():
        items = [x for x in selected if x[0] == campaign and x[1] == "original"]
        observed = (counts[(campaign, "original")], len(items),
                    sum(x[5] for x in items), sum(x[6] for x in items))
        expected = (expected_n, expected_selected, expected_success, expected_false)
        if observed != expected:
            raise ValueError(f"original campaign mismatch {campaign}: {observed} != {expected}")
        original[campaign] = {"decisions": observed[0], "selected": observed[1],
                              "successful": observed[2], "false_approvals": observed[3]}
    if len(counts) != 3 * 10 or len(seen) != 133 * 10:
        raise ValueError("unexpected campaign or scenario coverage")

    out = {}
    for campaign, scenario in sorted(counts):
        items = [x for x in selected if x[0] == campaign and x[1] == scenario]
        base_good = sum(x[5] for x in items)
        base_bad = sum(x[6] for x in items)
        thresholds = {}
        for threshold in THRESHOLDS:
            keep = [x for x in items if x[4] >= threshold]
            success = sum(x[5] for x in keep)
            false = sum(x[6] for x in keep)
            thresholds[f"{threshold:.2f}"] = {
                "selected": len(keep), "successful": success, "false_approvals": false,
                "successful_abstained_vs_zero": base_good - success,
                "false_approvals_removed_vs_zero": base_bad - false,
            }
        out[f"{campaign}/{scenario}"] = {
            "decisions": counts[(campaign, scenario)], "thresholds": thresholds,
            "false_approval_margins": [round(x[4], 6) for x in items if x[6]],
            "unique_false_workload_groups": sorted({x[2] for x in items if x[6]}),
        }
    return {
        "source_sha256": SOURCE_SHA256,
        "rows_total": len(rows),
        "stored_decisions": len(seen),
        "thresholds_diagnostic_only": list(THRESHOLDS),
        "original_reconciliation": original,
        "by_campaign_scenario": out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    actual = digest(SOURCE)
    if actual != SOURCE_SHA256:
        raise ValueError(f"source SHA-256 mismatch: {actual}")
    with SOURCE.open(newline="", encoding="utf-8") as stream:
        result = analyze(list(csv.DictReader(stream)))
    result["audit_script_sha256"] = digest(Path(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows_total": result["rows_total"],
                      "stored_decisions": result["stored_decisions"]}, sort_keys=True))


if __name__ == "__main__":
    main()
