#!/usr/bin/env python3
"""Read-only post-hoc audit of saved GenTD26 validation predictions.

Only train/validation request statuses are parsed. The source file's SHA-256
is checked, but iteration stops before inspecting the first test status.
This audit does not load or refit the frozen model and cannot score test rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/gentd26/lora_request_trace.csv"
PROTOCOL = RESEARCH / "reports/gentd26_request_failure_protocol.md"
FROZEN_SCRIPT = RESEARCH / "scripts/evaluate_gentd26_request_failure.py"
FROZEN_TESTS = RESEARCH / "tests/test_gentd26_request_failure.py"
FREEZE = RESEARCH / "artifacts/gentd26_request_failure_v1/freeze_manifest.json"
SELECTION = RESEARCH / "artifacts/gentd26_request_failure_v1/selection"
PREDICTIONS = SELECTION / "validation_predictions.csv"
METRICS = SELECTION / "selection_metrics.json"
MODEL = SELECTION / "model.joblib"
OUTPUT = RESEARCH / "artifacts/gentd26_request_failure_v1/posthoc_selection_audit.json"
EXPECTED_HASHES = {
    "source": "263fb88f1095f62d360cf89ded8cfc24fa2c5d111c1b16e7aaa9ccbfc4b37d78",
    "protocol": "d449343c4ef45190dd5b86cd3e5644e6193176180a21dfe2480a4107964ed729",
    "frozen_script": "b3edcc0ea9e4552196fa2271897fbd34e9aff1ca8ae454224924a2b52cfd55ab",
    "frozen_tests": "885a8fe47483eb5addc2435a6d4df166bfe1230efc1347f103710733680d8d6f",
    "freeze_manifest": "0ed06c00ed938acaa126076b555960d31a2abee3011042bfc43cc35665d0acd7",
    "predictions": "ea8d53f99b64c745bb02f2d68e28011adbb9585e5fd3cd177290c207efe06ecb",
    "metrics": "f4a999bfc25734d4ba05d01edde92596c04a343f03214b9c5e248d07addb19ef",
    "model": "36d1cdc178666a5e463fbc7252c1fd0c890bd9e76c8682a49affd9e5d8163034",
}
FILES = {
    "source": SOURCE, "protocol": PROTOCOL, "frozen_script": FROZEN_SCRIPT,
    "frozen_tests": FROZEN_TESTS, "freeze_manifest": FREEZE,
    "predictions": PREDICTIONS, "metrics": METRICS, "model": MODEL,
}
TRAIN_END = datetime.fromisoformat("2024-11-29 00:00:00")
VALID_END = datetime.fromisoformat("2024-12-04 00:00:00")
EPS = 1e-6


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_hashes() -> None:
    for name, path in FILES.items():
        actual = sha256(path)
        if actual != EXPECTED_HASHES[name]:
            raise ValueError(f"{name} hash changed: {actual}")
    frozen = json.loads(FREEZE.read_text())
    for field, name in (
        ("source_sha256", "source"), ("protocol_sha256", "protocol"),
        ("script_sha256", "frozen_script"), ("tests_sha256", "frozen_tests"),
    ):
        if frozen[field] != EXPECTED_HASHES[name]:
            raise ValueError(f"freeze manifest {field} does not match frozen file")
    if frozen["test_scoring_available"] is not False:
        raise ValueError("unexpected test scoring capability in freeze manifest")


def read_pretest_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    previous = None
    with SOURCE.open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"gmt_create", "predict_status", "checkpoint_model_version_id", "groupId"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("required source columns missing")
        for source_row, row in enumerate(reader, start=2):
            created = datetime.fromisoformat(row["gmt_create"])
            if previous is not None and created < previous:
                raise ValueError("source timestamps are not monotone")
            previous = created
            if created >= VALID_END:
                break
            status = row["predict_status"]
            if status in ("PENDING", "PROCESSING"):
                continue
            if status not in ("SUCCEED", "FAILED"):
                raise ValueError(f"unexpected pre-test status at row {source_row}")
            rows.append({
                "source_row": source_row,
                "day": created.date().isoformat(),
                "target": int(status == "FAILED"),
                "model": row["checkpoint_model_version_id"],
                "group": row["groupId"],
                "is_train": bool(created < TRAIN_END),
            })
    frame = pd.DataFrame(rows)
    train = frame.loc[frame.is_train].copy()
    valid = frame.loc[~frame.is_train].copy()
    if train.empty or valid.empty:
        raise ValueError("empty train or validation cohort")
    return train, valid


def joined_validation(valid: pd.DataFrame) -> pd.DataFrame:
    predictions = pd.read_csv(PREDICTIONS)
    if predictions.source_row.duplicated().any():
        raise ValueError("duplicate saved prediction source row")
    joined = valid.merge(predictions, on="source_row", how="outer", validate="one_to_one",
                         indicator=True, suffixes=("_source", "_saved"))
    if not joined._merge.eq("both").all():
        raise ValueError("source/prediction row mismatch")
    if not joined.target_source.eq(joined.target_saved).all():
        raise ValueError("source/prediction target mismatch")
    if not joined.day_source.eq(joined.day_saved).all():
        raise ValueError("source/prediction day mismatch")
    return joined.rename(columns={"target_source": "target", "day_source": "day"}).drop(
        columns=["target_saved", "day_saved", "_merge"]
    ).sort_values("source_row").reset_index(drop=True)


def score(data: pd.DataFrame, probabilities: np.ndarray) -> dict:
    y = data.target.to_numpy(int)
    p = np.asarray(probabilities, dtype=float)
    if not len(y) or len(y) != len(p) or not np.all(np.isfinite(p)):
        raise ValueError("invalid scored cohort")
    if (p < EPS).any() or (p > 1.0 - EPS).any():
        raise ValueError("prediction outside frozen clipping range")
    square = (y - p) ** 2
    by_day = {
        day: float(np.mean(square[indices]))
        for day, indices in data.groupby("day", sort=True).indices.items()
    }
    group_brier = [float(np.mean(square[indices]))
                   for indices in data.groupby("group", sort=False).indices.values()]
    return {
        "n": int(len(y)), "failed": int(y.sum()),
        "brier": float(np.mean(square)),
        "day_equal_brier": float(np.mean(list(by_day.values()))),
        "group_equal_brier": float(np.mean(group_brier)),
        "log_loss": float(np.mean(-(y * np.log(p) + (1 - y) * np.log1p(-p)))),
        "average_precision": float(average_precision_score(y, p)) if y.sum() else None,
        "mean_predicted_rate": float(np.mean(p)),
        "day_brier": by_day,
    }


def verify_saved_scores(data: pd.DataFrame, frozen: dict) -> dict:
    """Independently recompute all five predictors' saved validation metrics."""
    checked = {}
    for name in ("global", "type", "model", "model_type", "candidate"):
        computed = score(data, data[f"p_{name}"].to_numpy(float))
        saved = frozen["candidate_score"] if name == "candidate" else frozen["baseline_scores"][name]
        for key in ("n", "failed", "brier", "day_equal_brier", "group_equal_brier",
                    "log_loss", "average_precision", "mean_predicted_rate"):
            if not np.isclose(computed[key], saved[key], rtol=0, atol=1e-12):
                raise ValueError(f"saved {name} {key} disagrees with predictions")
        for day, value in computed["day_brier"].items():
            if not np.isclose(value, saved["days"][day]["brier"], rtol=0, atol=1e-12):
                raise ValueError(f"saved {name} day {day} Brier disagrees")
        checked[name] = computed
    return checked


def compare_group_familiarity(
    data: pd.DataFrame, train: pd.DataFrame, candidate: np.ndarray, baseline: np.ndarray,
) -> dict:
    seen = data.group.isin(set(train.group)).to_numpy(bool)
    result = {}
    for name, mask in (("seen_group", seen), ("new_group", ~seen)):
        part = data.loc[mask]
        result[name] = {
            "requests": int(len(part)), "failed": int(part.target.sum()),
            "groups": int(part.group.nunique()),
            "candidate_brier": score(part, candidate[mask])["brier"] if len(part) else None,
            "baseline_brier": score(part, baseline[mask])["brier"] if len(part) else None,
        }
    return result


def audit() -> dict:
    verify_hashes()
    train, valid = read_pretest_rows()
    data = joined_validation(valid)
    frozen = json.loads(METRICS.read_text())
    if (frozen["freeze_manifest_sha256"] != EXPECTED_HASHES["freeze_manifest"]
            or frozen["source_sha256"] != EXPECTED_HASHES["source"]):
        raise ValueError("saved selection does not refer to frozen source/manifest")
    recomputed = verify_saved_scores(data, frozen)
    if frozen["selected_baseline"] != "model_type" or frozen["test_score_opened"] is not False:
        raise ValueError("unexpected frozen selection/test status")
    if len(train) != frozen["support"]["train"]["requests"] or int(train.target.sum()) != frozen["support"]["train"]["failed"]:
        raise ValueError("train support differs from saved selection")
    if len(data) != frozen["support"]["validation"]["requests"] or int(data.target.sum()) != frozen["support"]["validation"]["failed"]:
        raise ValueError("validation support differs from saved selection")
    aggregate = train.groupby("model").target.agg(["sum", "count"])
    all_failed = aggregate.loc[aggregate["count"].ge(10) & aggregate["sum"].eq(aggregate["count"])]
    ids = sorted(all_failed.index.tolist())
    excluded = data.loc[data.model.isin(ids)]
    remainder = data.loc[~data.model.isin(ids)]
    if remainder.empty:
        raise ValueError("no validation rows after post-hoc model exclusion")
    baseline_col = data.p_model_type.to_numpy(float)
    candidate_col = data.p_candidate.to_numpy(float)
    keep = ~data.model.isin(ids).to_numpy(bool)
    after_candidate = score(remainder, candidate_col[keep])
    after_baseline = score(remainder, baseline_col[keep])
    posthoc_exclusion = {
        "rule": "train model count >= 10 and all train final statuses FAILED; post-hoc sensitivity",
        "model_ids": ids,
        "model_support": {
            name: {
                "train_requests": int(aggregate.loc[name, "count"]),
                "train_failed": int(aggregate.loc[name, "sum"]),
                "validation_requests": int((excluded.model == name).sum()),
                "validation_failed": int(excluded.loc[excluded.model.eq(name), "target"].sum()),
            }
            for name in ids
        },
        "removed_validation_requests": int(len(excluded)),
        "removed_validation_failed": int(excluded.target.sum()),
        "remaining_candidate": after_candidate,
        "remaining_selected_baseline": after_baseline,
        "candidate_day_wins": int(sum(
            after_candidate["day_brier"][day] < after_baseline["day_brier"][day]
            for day in after_candidate["day_brier"]
        )),
    }
    global_rate = (float(train.target.sum()) + 1.0) / (len(train) + 2.0)
    model_beta = (aggregate["sum"] + global_rate) / (aggregate["count"] + 1.0)
    beta_prob = data.model.map(model_beta).fillna(global_rate).to_numpy(float)
    beta_prob = np.clip(beta_prob, EPS, 1.0 - EPS)
    beta_score = score(data, beta_prob)
    beta_groups = compare_group_familiarity(data, train, candidate_col, beta_prob)
    beta_day_wins = int(sum(
        beta_score["day_brier"][day] < recomputed["candidate"]["day_brier"][day]
        for day in beta_score["day_brier"]
    ))
    return {
        "study": "post-hoc validation audit; never a predeclared comparator or independent test",
        "verified_files": {
            name: {"path": str(path.relative_to(RESEARCH)), "sha256": EXPECTED_HASHES[name]}
            for name, path in FILES.items()
        },
        "audit_script_sha256": sha256(Path(__file__)),
        "pretest_rows_only": True,
        "saved_primary_scores_recomputed": recomputed,
        "all_failed_model_exclusion": posthoc_exclusion,
        "posthoc_model_rate_one_pseudocount": {
            "formula": "global=(train_failed+1)/(train_n+2); model=(model_failed+global)/(model_n+1); unseen=global",
            "score": beta_score,
            "days_beating_candidate": beta_day_wins,
            "group_familiarity": beta_groups,
        },
        "test_score_opened": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = audit()
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output == Path("-"):
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(payload)
        print(json.dumps({"output": str(args.output), "sha256": sha256(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
