"""Donor-only margin/ABSTAIN calibration for the frozen zero-probe recommender.

This is a synthetic-SLO retrospective experiment on already explored SQLite.
It does not certify safe deployment, VM scaling, or live user-request SLOs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from cold_start_policy import STATIC, fold_partitions, prepare
from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, validate_slates


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/audit_v1/measurements.parquet"
OUT = ROOT / "artifacts/cold_start_selective_safety_v1"
PROTOCOL = ROOT / "reports/cold_start_selective_safety_protocol.md"
SOURCE_SHA = "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"
BASELINES = {"zero": 0.0, "fixed_005": 0.05, "fixed_020": 0.20}
CANDIDATES = (0.05, 0.10, 0.20, 0.30, 0.50)
MIN_QUERIES = 50
MIN_GROUPS = 5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_main() -> pd.DataFrame:
    if sha256(SOURCE) != SOURCE_SHA:
        raise ValueError("Frozen measurement snapshot hash mismatch")
    raw = pd.read_parquet(SOURCE)
    main = prepare(raw.loc[raw.campaign.eq(CAMPAIGN)])
    if len(main) != 4600 or main.model_id.nunique() != 9 or main.workload_group.nunique() != 575:
        raise ValueError("Cold-start slate changed")
    return main


def fit(donors: pd.DataFrame) -> ExtraTreesRegressor:
    model = ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2,
                                max_features=1.0, random_state=42, n_jobs=2)
    model.fit(donors[list(STATIC)].to_numpy(dtype=np.float32),
              donors[["true_log_latency", "true_log_rps"]].to_numpy(float))
    return model


def grids(donors: pd.DataFrame, target: pd.DataFrame, model: ExtraTreesRegressor) -> dict:
    if set(donors.model_id) & set(target.model_id):
        raise ValueError("Held-out model leaked into donor fit")
    thresholds = training_threshold_grid(donors)
    slate = target.sort_values(["workload_group", "replica_count", "cpu_core_limit"])
    validate_slates(slate)
    n_groups = slate.workload_group.nunique()
    if len(slate) != 8 * n_groups:
        raise ValueError("Expected eight resource candidates per workload")
    group_model = slate.model_id.to_numpy().reshape(n_groups, 8)
    if not np.all(group_model == group_model[:, :1]):
        raise ValueError("Mixed model IDs in workload group")
    pred = model.predict(slate[list(STATIC)].to_numpy(dtype=np.float32)).reshape(n_groups, 8, 2)
    true = slate[["true_log_latency", "true_log_rps"]].to_numpy(float).reshape(n_groups, 8, 2)
    limit = np.array([[np.log(item["latency_slo_ms"]), np.log(item["target_rps"])]
                      for item in thresholds], dtype=float)
    predicted_margin = np.minimum(limit[:, None, None, 0] - pred[None, :, :, 0],
                                  pred[None, :, :, 1] - limit[:, None, None, 1])
    truth = ((true[None, :, :, 0] <= limit[:, None, None, 0]) &
             (true[None, :, :, 1] >= limit[:, None, None, 1]))
    return dict(margin=predicted_margin, truth=truth,
                group=slate.workload_group.to_numpy().reshape(n_groups, 8)[:, 0],
                model=group_model[:, 0],
                config=slate[["replica_count", "cpu_core_limit"]].to_numpy().reshape(n_groups, 8, 2))


def choices(grid: dict, margin: float) -> dict:
    eligible = grid["margin"] >= margin
    selected = eligible.any(axis=2)
    rank = np.argmax(eligible, axis=2).astype(int)
    rank[~selected] = -1
    actual = grid["truth"]
    oracle = actual.any(axis=2)
    oracle_rank = np.argmax(actual, axis=2)
    chosen_truth = np.take_along_axis(actual, np.maximum(rank, 0)[..., None], axis=2)[..., 0]
    success = selected & chosen_truth
    false = selected & ~chosen_truth
    exact = success & (rank == oracle_rank)
    return dict(rank=rank, selected=selected, success=success, false=false,
                exact=exact, oracle=oracle, approved_groups=selected.any(axis=0))


def _counts(decision: dict) -> dict:
    selected = int(decision["selected"].sum())
    false = int(decision["false"].sum())
    success = int(decision["success"].sum())
    return dict(queries=int(decision["selected"].size), selected=selected,
                approved_groups=int(decision["approved_groups"].sum()),
                successful=success, false_approvals=false,
                false_approval_rate=(false / selected if selected else None),
                exact_minimal=int(decision["exact"].sum()),
                oracle_feasible=int(decision["oracle"].sum()),
                missed_feasible=int((decision["oracle"] & ~decision["success"]).sum()))


def select_threshold(inner: dict[str, dict[str, dict]]) -> tuple[float | None, dict]:
    """Select using only nested donor outcomes; None means ABSTAIN_ALL."""
    baseline_success = sum(by_margin["0.00"]["successful"] for by_margin in inner.values())
    result = {}
    for margin in CANDIDATES:
        key = f"{margin:.2f}"
        rows = [by_margin[key] for by_margin in inner.values()]
        selected = sum(row["selected"] for row in rows)
        successful = sum(row["successful"] for row in rows)
        false = sum(row["false_approvals"] for row in rows)
        floor = all(row["selected"] >= MIN_QUERIES and
                    row["approved_groups"] >= MIN_GROUPS for row in rows)
        valid = (baseline_success > 0
                 and all(by_margin["0.00"]["successful"] > 0 for by_margin in inner.values())
                 and floor and selected > 0 and false / selected <= 0.10
                 and successful >= 0.50 * baseline_success)
        result[key] = dict(selected=selected, successful=successful,
                           false_approvals=false,
                           false_approval_rate=(false / selected if selected else None),
                           each_donor_floor_met=floor,
                           retained_success_fraction=(successful / baseline_success
                                                      if baseline_success else None),
                           eligible=valid)
        if valid:
            return margin, result
    return None, result


def prepare_freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "freeze_manifest.json"
    if manifest_path.exists():
        raise FileExistsError("Frozen result already exists")
    main = load_main()
    folds = []
    for fold_name, donors, held in fold_partitions(main):
        inner = {}
        for donor_id in sorted(donors.model_id.unique()):
            fit_donors = donors.loc[donors.model_id.ne(donor_id)]
            pseudo_target = donors.loc[donors.model_id.eq(donor_id)]
            fitted = fit(fit_donors)
            grid = grids(fit_donors, pseudo_target, fitted)
            inner[donor_id] = {f"{margin:.2f}": _counts(choices(grid, margin))
                               for margin in (0.0,) + CANDIDATES}
        selected_margin, summary = select_threshold(inner)
        folds.append(dict(fold=fold_name, donor_models=sorted(donors.model_id.unique()),
                          held_models=sorted(held.model_id.unique()),
                          selected_margin=selected_margin,
                          selection=("ABSTAIN_ALL" if selected_margin is None else
                                     f"{selected_margin:.2f}"),
                          inner_donor_scores=inner, candidate_summary=summary))
        print("calibrated", fold_name, "selected", folds[-1]["selection"], flush=True)
    manifest = dict(source_sha256=SOURCE_SHA, protocol_sha256=sha256(PROTOCOL),
                    script_sha256=sha256(Path(__file__)),
                    model_spec="ExtraTreesRegressor(200, min_leaf=2, max_features=1, seed=42, n_jobs=2)",
                    fold_count=len(folds), folds=folds,
                    outer_scores_seen=False)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("freeze_manifest_sha256", sha256(manifest_path))


def _per_group(grid: dict, decision: dict, fold: str, method: str) -> pd.DataFrame:
    rows = pd.DataFrame({"fold": fold, "model_id": grid["model"],
                         "workload_group": grid["group"], "method": method,
                         "queries": decision["selected"].shape[0],
                         "selected": decision["selected"].sum(axis=0),
                         "successful": decision["success"].sum(axis=0),
                         "false_approvals": decision["false"].sum(axis=0),
                         "exact_minimal": decision["exact"].sum(axis=0),
                         "oracle_feasible": decision["oracle"].sum(axis=0),
                         "missed_feasible": (decision["oracle"] & ~decision["success"]).sum(axis=0),
                         "any_approved": decision["approved_groups"]})
    return rows


def _per_stratum(grid: dict, decision: dict, fold: str, method: str) -> pd.DataFrame:
    feasible_count = grid["truth"].sum(axis=2)
    strata = {"none": feasible_count == 0,
              "partial": (feasible_count > 0) & (feasible_count < 8),
              "all": feasible_count == 8}
    rows = []
    for model_id in np.unique(grid["model"]):
        belongs = grid["model"] == model_id
        for stratum, mask in strata.items():
            active = mask[:, belongs]
            selected = decision["selected"][:, belongs] & active
            success = decision["success"][:, belongs] & active
            false = decision["false"][:, belongs] & active
            rows.append(dict(fold=fold, model_id=model_id, method=method,
                             stratum=stratum, queries=int(active.sum()),
                             selected=int(selected.sum()), successful=int(success.sum()),
                             false_approvals=int(false.sum())))
    return pd.DataFrame(rows)


def _grouped_metrics(group: pd.DataFrame) -> pd.DataFrame:
    columns = ["queries", "selected", "successful", "false_approvals",
               "exact_minimal", "oracle_feasible", "missed_feasible"]
    out = group.groupby(["fold", "model_id", "method"], as_index=False)[columns].sum()
    approved = group.groupby(["fold", "model_id", "method"], as_index=False).any_approved.sum()
    out = out.merge(approved, on=["fold", "model_id", "method"], validate="one_to_one")
    out = out.rename(columns={"any_approved": "approved_groups"})
    out["false_approval_rate"] = out.false_approvals / out.selected.replace(0, np.nan)
    return out


def _bootstrap(group: pd.DataFrame, fold: str) -> dict:
    selected = group.loc[group.fold.eq(fold)]
    wide = selected.pivot(index=["model_id", "workload_group"],
                          columns="method", values=["successful", "false_approvals"])
    rng = np.random.default_rng(2026)
    result = {}
    for metric in ("successful", "false_approvals"):
        per_model = []
        for model_id, block in wide.groupby(level=0):
            diff = (block[(metric, "donor_selected")] - block[(metric, "fixed_005")]).to_numpy(float)
            draws = rng.integers(0, len(diff), size=(2000, len(diff)))
            per_model.append(diff[draws].sum(axis=1))
        sample = np.sum(np.stack(per_model), axis=0)
        result[metric] = dict(total_difference=float(sum(
            (block[(metric, "donor_selected")] - block[(metric, "fixed_005")]).sum()
            for _, block in wide.groupby(level=0))),
            workgroup_cluster_bootstrap_ci95=[float(x) for x in np.quantile(sample, [0.025, 0.975])],
            draws=2000, seed=2026)
    return result


def _gate(per_model: pd.DataFrame) -> dict:
    main = per_model.loc[per_model.fold.ne("bert_family_4")]
    totals = main.groupby("method")[["selected", "successful", "false_approvals"]].sum()
    chosen = totals.loc["donor_selected"]
    zero = totals.loc["zero"]
    fixed = totals.loc["fixed_005"]
    method_rows = main.loc[main.method.eq("donor_selected")].set_index("model_id")
    zero_rows = main.loc[main.method.eq("zero")].set_index("model_id")
    tests = dict(false_approvals_reduced_25pct=int(chosen.false_approvals) <= 0.75 * int(zero.false_approvals),
                 successful_retained_75pct=int(chosen.successful) >= 0.75 * int(zero.successful),
                 false_rate_no_worse_than_fixed_005=(chosen.selected > 0 and fixed.selected > 0 and
                     chosen.false_approvals / chosen.selected <= fixed.false_approvals / fixed.selected),
                 all_models_approval_floor=bool(((method_rows.selected >= MIN_QUERIES) &
                                                  (method_rows.approved_groups >= MIN_GROUPS)).all()),
                 every_model_retains_70pct_success=bool((method_rows.successful >=
                                                         0.70 * zero_rows.successful).all()))
    return dict(criteria=tests, all_pass=all(tests.values()))


def score_outer() -> None:
    manifest_path = OUT / "freeze_manifest.json"
    frozen = json.loads(manifest_path.read_text())
    if sha256(PROTOCOL) != frozen["protocol_sha256"] or sha256(Path(__file__)) != frozen["script_sha256"]:
        raise ValueError("Protocol or script changed after calibration freeze")
    if sha256(SOURCE) != frozen["source_sha256"]:
        raise ValueError("Source changed after calibration freeze")
    if (OUT / "outer_scores.json").exists():
        raise FileExistsError("Outer scores already exist")
    main = load_main()
    freeze_by_fold = {row["fold"]: row for row in frozen["folds"]}
    parts, strata_parts = [], []
    for fold_name, donors, held in fold_partitions(main):
        record = freeze_by_fold[fold_name]
        if (sorted(donors.model_id.unique()) != record["donor_models"] or
                sorted(held.model_id.unique()) != record["held_models"]):
            raise ValueError("Frozen fold membership changed")
        fitted = fit(donors)
        grid = grids(donors, held, fitted)
        methods = dict(BASELINES, donor_selected=(record["selected_margin"]
                                                  if record["selected_margin"] is not None else float("inf")))
        for method, margin in methods.items():
            decision = choices(grid, margin)
            parts.append(_per_group(grid, decision, fold_name, method))
            strata_parts.append(_per_stratum(grid, decision, fold_name, method))
        print("scored", fold_name, flush=True)
    group = pd.concat(parts, ignore_index=True)
    strata = pd.concat(strata_parts, ignore_index=True)
    per_model = _grouped_metrics(group)
    group.to_parquet(OUT / "outer_per_workgroup.parquet", index=False)
    per_model.to_csv(OUT / "outer_per_model.csv", index=False)
    strata.to_csv(OUT / "outer_by_stratum.csv", index=False)
    aggregate = per_model.groupby(["fold", "method"], as_index=False)[[
        "queries", "selected", "successful", "false_approvals", "exact_minimal",
        "oracle_feasible", "missed_feasible", "approved_groups"]].sum()
    main_totals = per_model.loc[per_model.fold.ne("bert_family_4")].groupby("method")[[
        "queries", "selected", "successful", "false_approvals", "exact_minimal",
        "oracle_feasible", "missed_feasible", "approved_groups"]].sum().reset_index()
    main_strata = strata.loc[strata.fold.ne("bert_family_4")].groupby(["method", "stratum"], as_index=False)[
        ["queries", "selected", "successful", "false_approvals"]].sum()
    main_group = group.loc[group.fold.ne("bert_family_4")].copy()
    main_group["fold"] = "main_loo"
    summary = dict(scope="exploratory reused SQLite, donor synthetic batch-SLO, no real deployment safety",
                   frozen_manifest_sha256=sha256(manifest_path),
                   outer_per_model_sha256=sha256(OUT / "outer_per_model.csv"),
                   outer_per_workgroup_sha256=sha256(OUT / "outer_per_workgroup.parquet"),
                   outer_by_stratum_sha256=sha256(OUT / "outer_by_stratum.csv"),
                   main_loo_aggregate=main_totals.to_dict(orient="records"),
                   main_loo_by_stratum=main_strata.to_dict(orient="records"),
                   bert_family_aggregate=aggregate.loc[aggregate.fold.eq("bert_family_4")].to_dict(orient="records"),
                   gate=_gate(per_model),
                   paired_workgroup_bootstrap=dict(main_loo=_bootstrap(main_group, "main_loo"),
                                                   bert_family_4=_bootstrap(group, "bert_family_4")))
    (OUT / "outer_scores.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "score"))
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare_freeze()
    else:
        score_outer()


if __name__ == "__main__":
    main()
