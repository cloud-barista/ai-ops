"""Posthoc model/family holdout evaluation of a frozen joint residual prior.

Every method sees the same eight target GPU1/CPU2 anchors. Donor residuals
come from inner leave-one-model-out fits. Saved legacy predictions and probe
manifests must match before any performance scores are written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_metric_anchor_transfer import joint_residual_prior, predict_with_joint_prior
from cross_model_covariance import (_absolute, _anchor_rows, residual_covariance,
                                    training_oof_anchor_residuals)
from cross_model_covariance_external import continuous_prior
from evaluate_counterfactual_slo import training_threshold_grid
from family_holdout_transfer import FAMILY, FAMILY_WITH_DISTILBERT, family_partition
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/audit_v1/measurements.parquet"
PROTOCOL = ROOT / "reports/cross_metric_anchor_transfer_protocol.md"
CORE = ROOT / "scripts/cross_metric_anchor_transfer.py"
PINNED = {
    SNAPSHOT: "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906",
    PROTOCOL: "db19815f033c556395cd80360d1c62f2df026118bb7e0a1bcf972f3a504de863",
    CORE: "0870b944a0365b847e6ca7563f81cacfaa01445c04e96fd31495e3bcac00bbf2",
    ROOT / "artifacts/cross_model_covariance_v1/predictions.parquet":
        "a1b110a6941b18bd52d0f45a07fcd6b89ea12de066baba47197a938b1ff480fb",
    ROOT / "artifacts/cross_model_covariance_v1/split_manifest.csv":
        "70e09d6a07f5b753224421975a7dd972d69b65850150aaa050492cde89049397",
    ROOT / "artifacts/cross_model_covariance_external_v1/predictions.parquet":
        "143900833fe35017dfeed14b653d7215d8696f40a566cd13ff0afe45efd9f32b",
    ROOT / "artifacts/cross_model_covariance_external_v1/split_manifest.csv":
        "d7d4fa0f42a5baa82bb4263bf217be749b4fce07c98e18ccb4da54c613485c62",
    ROOT / "artifacts/family_holdout_transfer_v1/predictions.parquet":
        "5b5d548a87999a87fd7e165b3c6573afaea8d7a4bfb26fa2f20264d23182e1a6",
    ROOT / "artifacts/family_holdout_transfer_v1/split_manifest.csv":
        "55c9f76b51ef82fdb4ecc53752a5f3b9e979a7d6abbdda5b7a54f2e1a57cd7be",
    ROOT / "artifacts/family_holdout_plus_distilbert_v1/predictions.parquet":
        "8b2810dc32247f7a3949cc54822334097813caa8e6b13e3bc49282e11f239263",
    ROOT / "artifacts/family_holdout_plus_distilbert_v1/split_manifest.csv":
        "1ec4dcade544bb98fe5f4c64dfb477286e5eac39f29d06161185947d5acd792d",
}
METHODS = ("joint_cross_metric", "joint_no_cross", "space_cov_transfer",
           "space_kernel_transfer", "space_ridge", "space_global_mean",
           "zero_shot")
CHECK_COLUMNS = tuple(f"{method}_{out}_log" for method in
                      ("space_cov_transfer", "space_kernel_transfer",
                       "space_ridge", "space_global_mean", "zero_shot")
                      for out in ("latency", "rps"))
CONFIGS = tuple((gpu, cores) for gpu in (1, 2) for cores in (2, 4, 8, 16))
REFERENCE_DIRS = {
    "loomo": (ROOT / "artifacts/cross_model_covariance_v1",
              ROOT / "artifacts/cross_model_covariance_external_v1"),
    "bert4": (ROOT / "artifacts/family_holdout_transfer_v1", None),
    "bert5": (ROOT / "artifacts/family_holdout_plus_distilbert_v1", None),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_pins() -> dict[str, str]:
    result = {}
    for path, expected in PINNED.items():
        actual = digest(path)
        if actual != expected:
            raise ValueError(f"frozen input SHA mismatch: {path.name}: {actual}")
        result[str(path.relative_to(ROOT))] = actual
    return result


def load_references() -> dict[str, dict]:
    refs = {}
    for split, (main_dir, external_dir) in REFERENCE_DIRS.items():
        primary = pd.read_parquet(main_dir / "predictions.parquet")
        if split == "loomo":
            primary = primary[primary.budget.eq(8)].copy()
        refs[split] = {
            "main": primary,
            "manifest": pd.read_csv(main_dir / "split_manifest.csv"),
            "external": (pd.read_parquet(external_dir / "predictions.parquet")
                         if external_dir else None),
            "external_manifest": (pd.read_csv(external_dir / "split_manifest.csv")
                                  if external_dir else None),
        }
    return refs


def check_probes(split: str, model_id: str, probes: list[str], cost: float,
                 references: dict[str, dict]) -> None:
    ref = references[split]
    manifest = ref["manifest"]
    if split == "loomo":
        rows = manifest[(manifest.heldout_model_id.eq(model_id)) &
                        manifest.budget.eq(8) & manifest.selector.eq("space_filling")]
        ext_rows = ref["external_manifest"][
            ref["external_manifest"].heldout_model_id.eq(model_id)]
        if len(ext_rows) != 1:
            raise AssertionError("external saved probe row missing")
        if json.loads(ext_rows.iloc[0].probe_groups_json) != probes:
            raise AssertionError("external probe locations changed")
        if not np.isclose(float(ext_rows.iloc[0].gpu_wall_main_probe_seconds), cost,
                          rtol=0, atol=1e-10):
            raise AssertionError("external probe cost changed")
    else:
        rows = manifest[manifest.heldout_model_id.eq(model_id)]
    if len(rows) != 1 or json.loads(rows.iloc[0].probe_groups_json) != probes:
        raise AssertionError("saved main probe locations changed")
    if not np.isclose(float(rows.iloc[0].gpu_wall_probe_seconds), cost,
                      rtol=0, atol=1e-10):
        raise AssertionError("saved main probe GPU-wall changed")


def check_saved_parity(pred: pd.DataFrame, reference: pd.DataFrame,
                       *, model_id: str, campaign: str) -> float:
    """Reproduced baselines must match all stored candidate predictions."""
    saved = reference[(reference.model_id.eq(model_id)) &
                      reference.campaign.eq(campaign)].copy()
    produced = pred[pred.campaign.eq(campaign)].copy()
    if (saved.empty or produced.empty or saved.execution_id.duplicated().any() or
            produced.execution_id.duplicated().any() or
            set(saved.execution_id) != set(produced.execution_id)):
        raise AssertionError("saved and reproduced candidate populations differ")
    saved = saved.set_index("execution_id").loc[sorted(saved.execution_id)]
    produced = produced.set_index("execution_id").loc[saved.index]
    for column in ("model_id", "workload_group", "replica_count", "cpu_core_limit"):
        if not (saved[column] == produced[column]).all():
            raise AssertionError(f"saved {column} changed")
    maximum = 0.0
    for column in CHECK_COLUMNS:
        actual = produced[column].to_numpy(dtype=float)
        expected = saved[column].to_numpy(dtype=float)
        if not np.isfinite(actual).all() or not np.isfinite(expected).all():
            raise AssertionError("nonfinite prediction in saved parity")
        maximum = max(maximum, float(np.max(np.abs(actual - expected))))
        if not np.allclose(actual, expected, rtol=1e-10, atol=1e-10):
            raise AssertionError(f"saved prediction parity failed: {column}")
    return maximum


def score_decisions(pred: pd.DataFrame, truth: pd.DataFrame,
                    thresholds: list[dict], *, split: str, campaign: str,
                    model_id: str) -> pd.DataFrame:
    """Use actual candidates only for retrospective success/oracle scoring."""
    slate = pred.merge(truth[["execution_id", "latency_p95_ms",
                              "examples_per_second", "batch_size"]],
                       on="execution_id", validate="one_to_one")
    slate = slate.sort_values(["workload_group", "replica_count", "cpu_core_limit"])
    groups = list(slate.workload_group.drop_duplicates())
    n = len(groups)
    if n == 0 or len(slate) != 8 * n or not np.array_equal(
            slate.workload_group.to_numpy().reshape(n, 8),
            np.broadcast_to(np.asarray(groups, dtype=object)[:, None], (n, 8))):
        raise ValueError("incomplete decision slate")
    configs = slate[["replica_count", "cpu_core_limit"]].to_numpy().reshape(n, 8, 2)
    if not np.array_equal(configs, np.broadcast_to(np.array(CONFIGS), configs.shape)):
        raise ValueError("resource order differs between predicted candidate slates")
    actual_l = slate.latency_p95_ms.to_numpy(dtype=float).reshape(n, 8)
    actual_r = (slate.examples_per_second / slate.batch_size).to_numpy(dtype=float).reshape(n, 8)
    original_l = slate.latency_slo_ms.to_numpy(dtype=float).reshape(n, 8)
    original_r = slate.target_throughput_rps.to_numpy(dtype=float).reshape(n, 8)
    if not (np.isfinite(actual_l).all() and np.isfinite(actual_r).all() and
            np.isfinite(original_l).all() and np.isfinite(original_r).all() and
            np.min(actual_l) > 0 and np.min(actual_r) > 0 and
            np.min(original_l) > 0 and np.min(original_r) > 0 and
            np.array_equal(original_l, np.broadcast_to(original_l[:, :1], (n, 8))) and
            np.array_equal(original_r, np.broadcast_to(original_r[:, :1], (n, 8)))):
        raise ValueError("invalid actual results or inconsistent original SLO")
    records = []
    for threshold in thresholds:
        scenario = threshold["scenario"]
        limits_l = (original_l[:, :1] if scenario == "original" else
                    np.full((n, 1), float(threshold["latency_slo_ms"])))
        limits_r = (original_r[:, :1] if scenario == "original" else
                    np.full((n, 1), float(threshold["target_rps"])))
        feasible = (actual_l <= limits_l) & (actual_r >= limits_r)
        oracle = feasible.any(axis=1)
        oracle_min = np.where(oracle, feasible.argmax(axis=1), -1)
        partly_feasible = oracle & (feasible.sum(axis=1) < 8)
        for method in METHODS:
            predicted_l = slate[f"{method}_latency_log"].to_numpy(dtype=float).reshape(n, 8)
            predicted_r = slate[f"{method}_rps_log"].to_numpy(dtype=float).reshape(n, 8)
            if not (np.isfinite(predicted_l).all() and np.isfinite(predicted_r).all()):
                raise ValueError("nonfinite predicted decision quantity")
            predicted = (predicted_l <= np.log(limits_l)) & (
                predicted_r >= np.log(limits_r))
            selected = predicted.any(axis=1)
            chosen = np.where(selected, predicted.argmax(axis=1), -1)
            success = selected & feasible[np.arange(n), np.maximum(chosen, 0)]
            wrong = selected & ~success
            exact_min = success & (chosen == oracle_min)
            records.append({
                "split": split, "campaign": campaign, "heldout_model_id": model_id,
                "scenario": scenario, "method": method,
                "latency_quantile": threshold.get("latency_quantile"),
                "rps_quantile": threshold.get("rps_quantile"),
                "workload_queries": n, "oracle_feasible": int(oracle.sum()),
                "partly_feasible": int(partly_feasible.sum()),
                "selected": int(selected.sum()),
                "successful_choices": int(success.sum()),
                "false_approvals": int(wrong.sum()),
                "missed_feasible": int((oracle & ~success).sum()),
                "exact_minimal": int(exact_min.sum()),
                "partly_feasible_success": int((partly_feasible & success).sum()),
            })
    return pd.DataFrame(records)


def primary_unmeasured_error(pred: pd.DataFrame, truth: pd.DataFrame,
                             probes: list[str], campaign: str,
                             method: str) -> dict[str, float | int]:
    """Score all truly unseen candidate cells, including external anchors."""
    if pred.execution_id.duplicated().any() or truth.execution_id.duplicated().any() or (
            set(pred.execution_id) != set(truth.execution_id)):
        raise ValueError("prediction and truth populations differ")
    joined = pred.merge(truth[["execution_id", "latency_p95_ms",
                              "examples_per_second", "batch_size"]],
                        on="execution_id", validate="one_to_one")
    measured = (joined.campaign.eq(CAMPAIGN) &
                joined.workload_group.isin(probes) &
                joined.replica_count.eq(1) & joined.cpu_core_limit.eq(2))
    if campaign == CAMPAIGN and int(measured.sum()) != 8:
        raise AssertionError("main split does not contain exactly eight measured anchors")
    if campaign != CAMPAIGN and measured.any():
        raise AssertionError("external target included a measured main anchor")
    unmeasured = joined.loc[~measured]
    expected = len(joined) - (8 if campaign == CAMPAIGN else 0)
    if len(unmeasured) != expected or expected == 0:
        raise AssertionError("incorrect unseen-cell scoring population")
    true_l = np.log(unmeasured.latency_p95_ms.to_numpy(dtype=float))
    true_r = np.log((unmeasured.examples_per_second /
                     unmeasured.batch_size).to_numpy(dtype=float))
    pred_l = unmeasured[f"{method}_latency_log"].to_numpy(dtype=float)
    pred_r = unmeasured[f"{method}_rps_log"].to_numpy(dtype=float)
    values = np.concatenate((true_l, true_r, pred_l, pred_r))
    if not np.isfinite(values).all():
        raise ValueError("nonfinite performance value")
    return {"unmeasured_candidates": len(unmeasured),
            "unmeasured_latency_male": float(np.abs(pred_l - true_l).mean()),
            "unmeasured_rps_male": float(np.abs(pred_r - true_r).mean())}


def _splits(main: pd.DataFrame):
    for model_id in sorted(main.model_id.unique()):
        yield "loomo", (model_id,), main[main.model_id != model_id]
    for name, family in (("bert4", FAMILY), ("bert5", FAMILY_WITH_DISTILBERT)):
        donors, _ = family_partition(main, family)
        yield name, family, donors


def summarize_frozen_gate(per_model: pd.DataFrame) -> tuple[list[dict], dict]:
    """Apply the protocol's predeclared adoption checks to primary errors."""
    shared = per_model[(per_model.split.eq("loomo")) &
                       ~per_model.heldout_model_id.str.startswith("Qwen/")].copy()
    if shared.heldout_model_id.nunique() != 8:
        raise AssertionError("expected eight shared-encoder LOOMO models")
    encoder_macro = shared.groupby(["campaign", "method"], as_index=False).agg(
        models=("heldout_model_id", "nunique"),
        scored_groups=("scored_groups", "sum"),
        unmeasured_candidates=("unmeasured_candidates", "sum"),
        unmeasured_latency_male=("unmeasured_latency_male", "mean"),
        unmeasured_rps_male=("unmeasured_rps_male", "mean"),
        feasible_groups=("feasible_groups", "sum"),
        successful_choices=("successful_choices", "sum"),
        slo_violations=("slo_violations", "sum"),
        missed_feasible_groups=("missed_feasible_groups", "sum"),
        exact_minimal=("exact_minimal", "sum"),
    )
    checks = []

    def compare(split: str, campaign: str, metric: str, baseline: str,
                *, strict: bool) -> None:
        table = encoder_macro if split == "encoder8" else per_model[
            (per_model.split.eq(split)) & per_model.campaign.eq(campaign)
        ].groupby("method", as_index=False).agg({
            metric: "sum" if metric == "slo_violations" else "mean"})
        if split == "encoder8":
            table = table[table.campaign.eq(campaign)]
        values = table.set_index("method")
        joint = float(values.loc["joint_cross_metric", metric])
        comparator = float(values.loc[baseline, metric])
        checks.append({"split": split, "campaign": campaign, "metric": metric,
                       "joint": joint, "baseline_method": baseline,
                       "baseline": comparator, "criterion": "<" if strict else "<=",
                       "passed": bool(joint < comparator if strict else
                                      joint <= comparator)})

    for metric in ("unmeasured_latency_male", "unmeasured_rps_male"):
        compare("encoder8", CAMPAIGN, metric, "joint_no_cross", strict=True)
    for campaign in EXTERNAL_CAMPAIGNS:
        for baseline in ("space_kernel_transfer", "space_ridge"):
            compare("encoder8", campaign, "unmeasured_rps_male", baseline,
                    strict=True)
    for split in ("bert4", "bert5"):
        for campaign in (CAMPAIGN, *EXTERNAL_CAMPAIGNS):
            for metric in ("unmeasured_latency_male", "unmeasured_rps_male",
                           "slo_violations"):
                compare(split, campaign, metric, "joint_no_cross", strict=False)
    return encoder_macro.to_dict(orient="records"), {
        "decision": "consider_adoption" if all(c["passed"] for c in checks)
                    else "do_not_adopt",
        "all_passed": bool(all(c["passed"] for c in checks)),
        "checks": checks,
        "basis": "frozen protocol: shared encoder8 primary errors; external RPS against both kernel and ridge; BERT4/BERT5 primary errors and original-SLO false approvals",
    }


def run(output: Path) -> dict:
    pins = verify_pins()
    raw = pd.read_parquet(SNAPSHOT)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible & raw.device.eq("cuda")].copy()
    external = raw[raw.campaign.isin(EXTERNAL_CAMPAIGNS) & raw.eligible &
                   raw.device.eq("cuda")].copy()
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError("main and new-condition workload groups overlap")
    references = load_references()
    predicted_parts, split_rows, parity_rows, prepared_splits = [], [], [], []
    for split, target_models, donor_raw in _splits(main):
        donor_ids = set(donor_raw.model_id.unique())
        if donor_ids & set(target_models):
            raise AssertionError("held-out model entered donor training")
        training = prepare(donor_raw)
        absolute = _absolute().fit(training[list(BASE_FEATURES)],
                                  training[["true_log_latency", "true_log_requests_per_second"]])
        conditional = fit_model(training, "anchor_conditioned", 42)
        donor_oof = training_oof_anchor_residuals(training)
        if set(donor_oof.model_id) != donor_ids:
            raise AssertionError("inner LOOMO donor IDs differ from outer training set")
        prepared_splits.append((split, tuple(target_models), donor_raw))
        for model_id in target_models:
            held_main = main[main.model_id.eq(model_id)].copy()
            held_external = external[external.model_id.eq(model_id)].copy()
            size = registered_parameter_bytes(held_main)
            design = group_design(static_features(held_main, size))
            probes = select_probes(design, 8, "medoids", 42)
            probe_rows = _anchor_rows(held_main).loc[probes]
            cost = float((probe_rows.replica_count * probe_rows.wall_seconds).sum())
            check_probes(split, model_id, probes, cost, references)

            legacy, _ = residual_covariance(held_main, donor_oof)
            joint, independent, joint_info = joint_residual_prior(held_main, donor_oof)
            pred_main = predict_with_joint_prior(held_main, probes, joint,
                                                 independent, legacy, absolute,
                                                 conditional, parameter_bytes=size)
            if joint_info["mode"] == "independent_kernel_fallback":
                for output_name in ("latency", "rps"):
                    a = pred_main[f"joint_cross_metric_{output_name}_log"].to_numpy()
                    b = pred_main[f"joint_no_cross_{output_name}_log"].to_numpy()
                    if not np.allclose(a, b, rtol=1e-10, atol=1e-10):
                        raise AssertionError("unsupported-model fallback changed prediction: "
                                             f"{output_name} max_abs={np.max(np.abs(a-b))}")
            max_diff = check_saved_parity(pred_main, references[split]["main"],
                                          model_id=model_id, campaign=CAMPAIGN)
            pred_main["split"] = split
            pred_main["heldout_model_id"] = model_id
            predicted_parts.append(pred_main)
            parity_rows.append({"split": split, "heldout_model_id": model_id,
                                "campaign": CAMPAIGN, "max_saved_prediction_abs_diff": max_diff})
            split_rows.append({"split": split, "heldout_model_id": model_id,
                               "training_model_ids_json": json.dumps(sorted(donor_ids)),
                               "probe_groups_json": json.dumps(probes),
                               "probe_budget": 8, "external_probe_budget": 0,
                               "probe_gpu_wall_seconds": cost,
                               "main_prior_mode": joint_info["mode"],
                               "main_minimum_eigenvalue": joint_info["minimum_eigenvalue"]})
            if not held_external.empty:
                if set(probes) & set(held_external.workload_group):
                    raise AssertionError("an external condition was used as a main anchor")
                all_target = pd.concat([held_main, held_external], ignore_index=True)
                legacy_external, _ = continuous_prior(all_target, donor_oof)
                joint_external, independent_external, external_info = joint_residual_prior(
                    all_target, donor_oof, interpolate=True)
                pred_external = predict_with_joint_prior(
                    all_target, probes, joint_external, independent_external,
                    legacy_external, absolute, conditional, parameter_bytes=size)
                pred_external = pred_external[
                    pred_external.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
                pred_external["split"] = split
                pred_external["heldout_model_id"] = model_id
                predicted_parts.append(pred_external)
                split_rows[-1]["external_prior_mode"] = external_info["mode"]
                split_rows[-1]["external_minimum_eigenvalue"] = external_info[
                    "minimum_eigenvalue"]
                if split == "loomo":
                    for campaign in EXTERNAL_CAMPAIGNS:
                        if not pred_external.campaign.eq(campaign).any():
                            continue
                        diff = check_saved_parity(pred_external,
                                                  references[split]["external"],
                                                  model_id=model_id, campaign=campaign)
                        parity_rows.append({"split": split, "heldout_model_id": model_id,
                                            "campaign": campaign,
                                            "max_saved_prediction_abs_diff": diff})
            print(f"predicted {split} {model_id}", flush=True)

    # No performance labels are scored until all pinned-file, probe-cost,
    # baseline-prediction, PSD and independent-ablation parity checks pass.
    predictions = pd.concat(predicted_parts, ignore_index=True)
    ablation_max_diff = 0.0
    for output_name in ("latency", "rps"):
        independent = predictions[f"joint_no_cross_{output_name}_log"].to_numpy()
        legacy = predictions[f"space_cov_transfer_{output_name}_log"].to_numpy()
        ablation_max_diff = max(ablation_max_diff,
                                float(np.max(np.abs(independent - legacy))))
        if not np.allclose(independent, legacy, rtol=1e-10, atol=1e-10):
            raise AssertionError(f"joint_no_cross differs from saved independent "
                                 f"method for {output_name}")
    probe_lookup = {(row["split"], row["heldout_model_id"]):
                    json.loads(row["probe_groups_json"]) for row in split_rows}
    decisions, errors = [], []
    for split, target_models, donors in prepared_splits:
        grid = [{"scenario": "synthetic", **row} for row in training_threshold_grid(donors)]
        thresholds = [{"scenario": "original"}] + grid
        for model_id in target_models:
            for campaign in (CAMPAIGN, *EXTERNAL_CAMPAIGNS):
                truth = (main if campaign == CAMPAIGN else external)
                truth = truth[(truth.model_id.eq(model_id)) & truth.campaign.eq(campaign)]
                if truth.empty:
                    continue
                pred = predictions[(predictions.split.eq(split)) &
                                   predictions.model_id.eq(model_id) &
                                   predictions.campaign.eq(campaign)]
                original = scenario_metrics(pred, truth, methods=METHODS)
                probes = probe_lookup[(split, model_id)]
                scored = score_decisions(pred, truth, thresholds, split=split,
                                         campaign=campaign, model_id=model_id)
                for method in METHODS:
                    primary = primary_unmeasured_error(pred, truth, probes,
                                                       campaign, method)
                    expected_count = ((496 if model_id == "Qwen/Qwen2.5-0.5B-Instruct"
                                       else 504) if campaign == CAMPAIGN else len(truth))
                    if primary["unmeasured_candidates"] != expected_count:
                        raise AssertionError("frozen unseen-cell row count changed")
                    row = scored[(scored.scenario.eq("original")) &
                                 scored.method.eq(method)].iloc[0]
                    if (original[method]["successful_choices"] != row.successful_choices or
                            original[method]["slo_violations"] != row.false_approvals or
                            original[method]["missed_feasible_groups"] != row.missed_feasible):
                        raise AssertionError("two original-SLO scoring paths disagree")
                    legacy = original[method]
                    errors.append({"split": split, "campaign": campaign,
                                   "heldout_model_id": model_id, "method": method,
                                   "scored_groups": legacy["scored_groups"],
                                   **primary,
                                   "nonanchor_candidates": legacy["scored_candidates"],
                                   "nonanchor_latency_male": legacy["latency_male"],
                                   "nonanchor_rps_male": legacy["rps_male"],
                                   "feasible_groups": legacy["feasible_groups"],
                                   "chosen_groups": legacy["chosen_groups"],
                                   "successful_choices": legacy["successful_choices"],
                                   "slo_violations": legacy["slo_violations"],
                                   "missed_feasible_groups": legacy["missed_feasible_groups"],
                                   "exact_minimal": int(row.exact_minimal),
                                   "partly_feasible": int(row.partly_feasible),
                                   "partly_feasible_success": int(row.partly_feasible_success)})
                decisions.append(scored)
    per_model = pd.DataFrame(errors)
    scenario_rows = pd.concat(decisions, ignore_index=True)
    macro = per_model.groupby(["split", "campaign", "method"], as_index=False).agg(
        models=("heldout_model_id", "nunique"),
        scored_groups=("scored_groups", "sum"),
        unmeasured_candidates=("unmeasured_candidates", "sum"),
        unmeasured_latency_male=("unmeasured_latency_male", "mean"),
        unmeasured_rps_male=("unmeasured_rps_male", "mean"),
        nonanchor_candidates=("nonanchor_candidates", "sum"),
        nonanchor_latency_male=("nonanchor_latency_male", "mean"),
        nonanchor_rps_male=("nonanchor_rps_male", "mean"),
        feasible_groups=("feasible_groups", "sum"),
        chosen_groups=("chosen_groups", "sum"),
        successful_choices=("successful_choices", "sum"),
        slo_violations=("slo_violations", "sum"),
        missed_feasible_groups=("missed_feasible_groups", "sum"),
        exact_minimal=("exact_minimal", "sum"),
    )
    encoder_macro, frozen_gate = summarize_frozen_gate(per_model)
    output.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(output / "predictions.parquet", index=False)
    pd.DataFrame(split_rows).to_csv(output / "split_manifest.csv", index=False)
    pd.DataFrame(parity_rows).to_csv(output / "parity.csv", index=False)
    per_model.to_csv(output / "per_model_metrics.csv", index=False)
    scenario_rows.to_csv(output / "slo_scenarios.csv", index=False)
    macro.to_csv(output / "macro.csv", index=False)
    summary = {
        "scope": "posthoc exploration on exposed local SQLite; not an independent confirmation or user-SLO/VM action effect",
        "pinned_input_sha256": pins,
        "runner_sha256": digest(Path(__file__)),
        "dependencies_sha256": {str(p.relative_to(ROOT)): digest(p) for p in (
            ROOT / "scripts/cross_model_covariance.py",
            ROOT / "scripts/cross_model_covariance_external.py",
            ROOT / "scripts/conditional_response.py",
            ROOT / "scripts/sparse_anchor_transfer.py",
            ROOT / "scripts/evaluate_counterfactual_slo.py",
            ROOT / "scripts/family_holdout_transfer.py")},
        "output_sha256": {name: digest(output / name) for name in (
            "predictions.parquet", "split_manifest.csv", "parity.csv",
            "per_model_metrics.csv", "slo_scenarios.csv", "macro.csv")},
        "split": "outer LOOMO 9 models, BERT4 and BERT+DistilBERT5 joint holdouts; inner donor LOOMO",
        "target_observations": "same eight GPU1/CPU2 main-campaign anchors per model; zero external probes",
        "primary_error_population": "all unmeasured shape cells: main excludes only eight measured GPU1/CPU2 anchors per model; external includes all eight cells per workload",
        "legacy_error_population": "seven non-anchor configurations per workload, retained solely for comparison with prior artifacts",
        "method_ablation": "joint_no_cross equals original space_cov_transfer prediction for every row",
        "max_independent_ablation_abs_diff": ablation_max_diff,
        "prediction_rows": len(predictions),
        "probe_cost": "observed sum(replica_count * wall_seconds) on identical main probes; not VM price",
        "slo_scope": "original generated batch SLO plus 25 donor-only quantile pairs per split; correlated repeated workload queries",
        "macro": macro.to_dict(orient="records"),
        "encoder8_macro": encoder_macro,
        "frozen_adoption_gate": frozen_gate,
        "max_saved_prediction_abs_diff": float(pd.DataFrame(parity_rows).
                                                max_saved_prediction_abs_diff.max()),
    }
    (output / "metrics.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/cross_metric_anchor_transfer_v1")
    args = parser.parse_args()
    result = run(args.output)
    for row in result["macro"]:
        if row["method"] in ("joint_cross_metric", "space_cov_transfer"):
            print(row, flush=True)
