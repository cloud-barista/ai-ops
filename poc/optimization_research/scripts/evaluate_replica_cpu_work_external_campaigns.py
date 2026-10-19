"""Frozen same-model/same-host out-of-campaign CPU-work stress test."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import evaluate_replica_cpu_work as original


PROTOCOL = ROOT / "reports/replica_cpu_work_external_campaign_protocol_v1.md"
OUTPUT = ROOT / "artifacts/replica_cpu_work_external_campaign_v1"
FREEZE = OUTPUT / "freeze.json"
CAMPAIGNS = ("khu-hf-live", "khu-hf-diverse-cpu-matrix")
EXPECTED = {
    "khu-hf-live": {"complete": 660, "valid": 659, "gpu1_only": 440,
                    "input_encoding_mismatch_qwen": 1, "encoder": 550, "qwen": 109},
    "khu-hf-diverse-cpu-matrix": {"complete": 48, "valid": 48,
                                   "gpu1_only": 32, "input_encoding_mismatch_qwen": 0,
                                   "encoder": 40, "qwen": 8},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_original_freeze() -> None:
    frozen = json.loads(original.FREEZE.read_text())
    required = {
        "snapshot_sha256": original.sha256(original.SNAPSHOT),
        "protocol_sha256": original.sha256(original.PROTOCOL),
        "script_sha256": original.sha256(Path(original.__file__)),
    }
    if any(frozen.get(key) != value for key, value in required.items()):
        raise ValueError("Original CPU-work study no longer matches its freeze")


def extract_external(snapshot: pd.DataFrame, campaign: str) -> tuple[pd.DataFrame, dict]:
    if campaign not in EXPECTED:
        raise ValueError("Unsupported external campaign")
    rows = snapshot.loc[(snapshot.campaign == campaign) & snapshot.eligible &
                        (snapshot.success == 1) & (snapshot.host == "icns") &
                        (snapshot.device == "cuda") &
                        snapshot.replica_count.isin([1, 2])]
    groups: dict[tuple, dict[int, dict]] = {}
    for row in rows.to_dict("records"):
        key = (row["run_id"],) + tuple(row[name] for name in original.PAIR_KEY)
        replica = original.positive_int(row["replica_count"], "replica_count")
        group = groups.setdefault(key, {})
        if replica in group:
            raise ValueError(f"Duplicate run-level measurement: {key}, {replica}")
        group[replica] = row
    counts = Counter()
    pairs = []
    for key in sorted(groups):
        group = groups[key]
        if set(group) == {1}:
            counts["gpu1_only"] += 1
            continue
        if set(group) != {1, 2}:
            raise ValueError(f"GPU2 without GPU1: {key}")
        counts["complete"] += 1
        first, second = group[1], group[2]
        if first["collector_version"] != second["collector_version"]:
            raise ValueError(f"Collector version differs across pair: {key}")
        try:
            record = original.pair_to_record(first, second)
        except ValueError as exc:
            if (campaign == "khu-hf-live" and
                    first["model_id"] == "Qwen/Qwen2.5-0.5B-Instruct" and
                    str(exc) == "GPU1/GPU2 static profile mismatch: input_encoding" and
                    first["input_encoding"] == "unspecified" and
                    second["input_encoding"] == "tokenizer_text"):
                counts["input_encoding_mismatch_qwen"] += 1
                continue
            raise
        record["run_id"] = first["run_id"]
        record["collector_version"] = first["collector_version"]
        pairs.append(record)
        counts["valid"] += 1
        counts["encoder" if record["workload_task"] == "inference" else "qwen"] += 1
    audit = {name: int(counts[name]) for name in EXPECTED[campaign]}
    if audit != EXPECTED[campaign]:
        raise ValueError(f"External campaign audit differs from prespecified counts: {campaign}: {audit}")
    result = pd.DataFrame(pairs)
    if result.duplicated(["run_id", *original.PAIR_KEY]).any():
        raise ValueError("External pairs are not unique by run and profile")
    return result, audit


def load_audit() -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    verify_original_freeze()
    if sha256(original.SNAPSHOT) != original.EXPECTED_SNAPSHOT_SHA256:
        raise ValueError("Source extraction changed")
    snapshot = pd.read_parquet(original.SNAPSHOT)
    pairs, audits = {}, {}
    main = original.extract_pairs(snapshot, original.MAIN, 2300)
    train = main.loc[main.workload_task == "inference"]
    if len(train) != 2048 or train.model_id.nunique() != 8:
        raise ValueError("Original donor coverage changed")
    main_started = pd.to_datetime(snapshot.loc[snapshot.campaign == original.MAIN, "started_at"]).min()
    for campaign in CAMPAIGNS:
        frame, audit = extract_external(snapshot, campaign)
        encoder_ids = set(frame.loc[frame.workload_task == "inference", "model_id"])
        if len(encoder_ids) != 5 or not encoder_ids.issubset(set(train.model_id)):
            raise ValueError("External encoder model has no leave-model-out fold")
        if set(frame.workload_group) & set(main.workload_group):
            raise ValueError("External workload groups overlap main campaign")
        latest_external = pd.to_datetime(snapshot.loc[snapshot.campaign == campaign, "started_at"]).max()
        if latest_external >= main_started:
            raise ValueError("External campaign was not collected before main campaign")
        pairs[campaign], audits[campaign] = frame, audit
    ids_by_campaign = [set(frame.loc[frame.workload_task == "inference", "model_id"])
                       for frame in pairs.values()]
    if ids_by_campaign[0] != ids_by_campaign[1]:
        raise ValueError("External campaigns contain different encoder model sets")
    return pairs, audits


def provenance(audits: dict[str, dict]) -> dict:
    return {
        "source_sha256": sha256(original.SNAPSHOT),
        "original_freeze_sha256": sha256(original.FREEZE),
        "original_script_sha256": sha256(Path(original.__file__)),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "audits": audits,
        "target": "same_host_other_campaign_gpu2_total_child_cpu_seconds",
    }


def freeze() -> None:
    _, audits = load_audit()
    contents = provenance(audits)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if FREEZE.exists():
        if json.loads(FREEZE.read_text()) != contents:
            raise ValueError("Existing freeze disagrees with current code or data")
        print(f"Verified existing freeze: {FREEZE}")
        return
    FREEZE.write_text(json.dumps(contents, indent=2, sort_keys=True) + "\n")
    print(f"Wrote pre-score freeze: {FREEZE}, sha256={sha256(FREEZE)}")


def evaluate() -> None:
    if not FREEZE.exists():
        raise ValueError("Freeze missing; scoring refused")
    pairs, audits = load_audit()
    if json.loads(FREEZE.read_text()) != provenance(audits):
        raise ValueError("Source, original model, protocol, or code differs from freeze")
    snapshot = pd.read_parquet(original.SNAPSHOT)
    main = original.extract_pairs(snapshot, original.MAIN, 2300)
    main = main.loc[main.workload_task == "inference"]
    predictions = []
    for held_model_id in sorted(set(pairs[CAMPAIGNS[0]].loc[
            pairs[CAMPAIGNS[0]].workload_task == "inference", "model_id"])):
        donor = main.loc[main.model_id != held_model_id]
        median, ridge, hgb = original.fit_fold(donor, held_model_id)
        for campaign in CAMPAIGNS:
            target = pairs[campaign].loc[(pairs[campaign].workload_task == "inference") &
                                         (pairs[campaign].model_id == held_model_id)]
            output = original.predict_fold(target, median, ridge, hgb)
            output["run_id"] = target.run_id.to_numpy()
            output["collector_version"] = target.collector_version.to_numpy()
            predictions.append(output)
    predicted = pd.concat(predictions, ignore_index=True)
    scores = {campaign: original.score_campaign(predicted.loc[predicted.campaign == campaign])
              for campaign in CAMPAIGNS}
    live = CAMPAIGNS[0]
    collector_scores = {
        version: original.score_campaign(predicted.loc[(predicted.campaign == live) &
                                                         (predicted.collector_version == version)])
        for version in sorted(predicted.loc[predicted.campaign == live, "collector_version"].unique())
    }
    checks = {}
    for campaign in CAMPAIGNS:
        score = scores[campaign]
        checks[f"{campaign}_mean_5pct_better_than_ridge"] = (
            score["macro"]["hgb_ratio"]["mean_abs_log_error"] <=
            0.95 * score["macro"]["ridge_ratio"]["mean_abs_log_error"])
        by_model = {}
        for row in score["per_model"]:
            by_model.setdefault(row["model_id"], {})[row["method"]] = row["mean_abs_log_error"]
        checks[f"{campaign}_every_model_within_120pct_ridge"] = all(
            values["hgb_ratio"] <= 1.20 * values["ridge_ratio"]
            for values in by_model.values())
    for version, score in collector_scores.items():
        checks[f"live_{version}_not_worse_than_ridge"] = (
            score["macro"]["hgb_ratio"]["mean_abs_log_error"] <=
            score["macro"]["ridge_ratio"]["mean_abs_log_error"])
    result = {
        "status": "exploratory_same_model_same_host_other_campaign",
        "freeze_sha256": sha256(FREEZE),
        "audits": audits,
        "scores": scores,
        "live_collector_scores": collector_scores,
        "stability_gate": {"passed": all(checks.values()), "checks": checks},
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    predicted.to_csv(OUTPUT / "predictions.csv", index=False)
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"stability_gate": result["stability_gate"],
                      "macro": {name: {method: values["mean_abs_log_error"]
                                       for method, values in score["macro"].items()}
                                for name, score in scores.items()}}, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "evaluate"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    else:
        evaluate()


if __name__ == "__main__":
    main()
