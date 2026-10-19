"""Serialize already-scored cold-start margin artifacts after a NumPy bool JSON error.

This script reads saved fold/workgroup scores only. It never fits or scores a
model, changes a threshold, or reads target measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_cold_start_selective_safety import (OUT, PROTOCOL, SOURCE,
                                                   _bootstrap, _gate, sha256)


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unexpected JSON type: {type(value).__name__}")


def finalize() -> dict:
    frozen = json.loads((OUT / "freeze_manifest.json").read_text())
    frozen_script = Path(__file__).with_name("evaluate_cold_start_selective_safety.py")
    if (sha256(frozen_script) != frozen["script_sha256"] or
            sha256(PROTOCOL) != frozen["protocol_sha256"] or
            sha256(SOURCE) != frozen["source_sha256"]):
        raise ValueError("Frozen input changed after outer scoring")
    output = OUT / "outer_scores.json"
    if output.exists():
        raise FileExistsError("Outer score summary already exists")
    per_model = pd.read_csv(OUT / "outer_per_model.csv")
    group = pd.read_parquet(OUT / "outer_per_workgroup.parquet")
    strata = pd.read_csv(OUT / "outer_by_stratum.csv")
    columns = ["queries", "selected", "successful", "false_approvals",
               "exact_minimal", "oracle_feasible", "missed_feasible", "approved_groups"]
    aggregate = per_model.groupby(["fold", "method"], as_index=False)[columns].sum()
    main_totals = per_model.loc[per_model.fold.ne("bert_family_4")].groupby("method")[columns].sum().reset_index()
    main_strata = strata.loc[strata.fold.ne("bert_family_4")].groupby(["method", "stratum"], as_index=False)[
        ["queries", "selected", "successful", "false_approvals"]].sum()
    main_group = group.loc[group.fold.ne("bert_family_4")].copy()
    main_group["fold"] = "main_loo"
    result = dict(scope="exploratory reused SQLite, donor synthetic batch-SLO, no real deployment safety",
                  note="Outer scores were saved by frozen script; JSON serialization failed on np.bool_. This finalizer did not rescore or retune.",
                  frozen_manifest_sha256=sha256(OUT / "freeze_manifest.json"),
                  frozen_script_sha256=sha256(frozen_script),
                  finalizer_script_sha256=sha256(Path(__file__)),
                  outer_per_model_sha256=sha256(OUT / "outer_per_model.csv"),
                  outer_per_workgroup_sha256=sha256(OUT / "outer_per_workgroup.parquet"),
                  outer_by_stratum_sha256=sha256(OUT / "outer_by_stratum.csv"),
                  main_loo_aggregate=main_totals.to_dict(orient="records"),
                  main_loo_by_stratum=main_strata.to_dict(orient="records"),
                  bert_family_aggregate=aggregate.loc[aggregate.fold.eq("bert_family_4")].to_dict(orient="records"),
                  gate=_gate(per_model),
                  paired_workgroup_bootstrap=dict(main_loo=_bootstrap(main_group, "main_loo"),
                                                  bert_family_4=_bootstrap(group, "bert_family_4")))
    output.write_text(json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n")
    return result


if __name__ == "__main__":
    report = finalize()
    print(json.dumps(report, indent=2, default=_json_default))
