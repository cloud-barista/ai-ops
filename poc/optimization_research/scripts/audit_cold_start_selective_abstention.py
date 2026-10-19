"""Disambiguate 'missed feasible' from truly abstained feasible queries.

Uses frozen outer per-model and feasibility-stratum aggregates only. It never
re-fits a model, changes a threshold, or reads raw target measurements.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


OUT = Path(__file__).resolve().parents[1] / "artifacts/cold_start_selective_safety_v1"
KEYS = ["fold", "model_id", "method"]


def audit(per_model: pd.DataFrame, strata: pd.DataFrame) -> pd.DataFrame:
    if per_model.duplicated(KEYS).any() or strata.duplicated(KEYS + ["stratum"]).any():
        raise ValueError("Duplicate fold/model/method aggregates")
    totals = strata.groupby(KEYS)[["queries", "selected", "successful", "false_approvals"]].sum().reset_index()
    joined = per_model.merge(totals, on=KEYS, suffixes=("", "_strata"), validate="one_to_one")
    if len(joined) != len(per_model):
        raise ValueError("Missing feasibility strata")
    for col in ("queries", "selected", "successful", "false_approvals"):
        if not (joined[col] == joined[f"{col}_strata"]).all():
            raise ValueError(f"Inconsistent {col} between artifacts")
    none_false = strata.loc[strata.stratum.eq("none"), KEYS + ["false_approvals"]]
    none_false = none_false.rename(columns={"false_approvals": "impossible_query_wrong_approvals"})
    result = per_model.merge(none_false, on=KEYS, validate="one_to_one")
    if len(result) != len(per_model):
        raise ValueError("Missing no-feasible stratum")
    result["feasible_query_wrong_approvals"] = (result.false_approvals -
                                                 result.impossible_query_wrong_approvals)
    result["feasible_query_abstentions"] = (result.oracle_feasible - result.successful -
                                             result.feasible_query_wrong_approvals)
    if (result[["feasible_query_wrong_approvals", "feasible_query_abstentions"]] < 0).any().any():
        raise ValueError("Negative disjoint category count")
    if not (result.missed_feasible == result.feasible_query_wrong_approvals +
            result.feasible_query_abstentions).all():
        raise ValueError("Original missed_feasible does not match disjoint categories")
    return result[KEYS + ["queries", "oracle_feasible", "selected", "successful",
                          "false_approvals", "missed_feasible",
                          "impossible_query_wrong_approvals", "feasible_query_wrong_approvals",
                          "feasible_query_abstentions"]]


if __name__ == "__main__":
    result = audit(pd.read_csv(OUT / "outer_per_model.csv"),
                   pd.read_csv(OUT / "outer_by_stratum.csv"))
    result.to_csv(OUT / "outer_abstention_audit.csv", index=False)
    print(result.groupby("method")[["feasible_query_abstentions",
                                    "feasible_query_wrong_approvals"]].sum().to_string())
