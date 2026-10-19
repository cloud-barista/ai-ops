"""Offline reward-model training and guarded inference for KHU scenarios."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
import random
import sqlite3
from statistics import mean
from typing import Any, Mapping, Sequence

import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer


MODEL_TYPE = "random_forest_reward_regressor"
MODEL_VERSION = "khu-policy-rf-v1"
FEATURE_SCHEMA_VERSION = "khu-policy-feature-v1"


@dataclass(frozen=True)
class TrainingRow:
    scenario_id: str
    source_scenario_id: str
    candidate_id: str
    action: str
    features: dict[str, float | str]
    reward: float
    feasible: bool
    slo_satisfied: bool
    is_oracle: bool


@dataclass
class PolicyModel:
    vectorizer: DictVectorizer
    model: RandomForestRegressor
    manifest: Mapping[str, Any]

    def predict(self, features: Mapping[str, float | str]) -> dict[str, Any]:
        matrix = self.vectorizer.transform([dict(features)])
        expected = float(self.model.predict(matrix)[0])
        values = [float(tree.predict(matrix)[0]) for tree in self.model.estimators_]
        spread = sqrt(sum((item - expected) ** 2 for item in values) / max(1, len(values)))
        ood_ratio, warnings = _ood(features, self.manifest)
        normalized_spread = min(1.0, spread / 0.50)
        confidence = max(0.0, min(1.0, 0.95 * (1.0 - normalized_spread) * (1.0 - ood_ratio)))
        return {
            "expected_utility": round(max(0.0, min(1.0, expected)), 8),
            "ensemble_stddev": round(spread, 8),
            "confidence": round(confidence, 8),
            "ood_ratio": round(ood_ratio, 8),
            "warnings": warnings,
        }


def train_policy(
    *,
    dataset_db: str | Path,
    artifact_dir: str | Path,
    seed: int = 42,
    trees: int = 200,
) -> dict[str, Any]:
    rows = load_rows(dataset_db)
    if not rows:
        raise ValueError("KHU dataset has no action outcomes")
    splits = _group_split(rows, seed)
    vectorizer = DictVectorizer(sparse=True, sort=True)
    train_matrix = vectorizer.fit_transform([row.features for row in splits["train"]])
    model = RandomForestRegressor(
        n_estimators=int(trees),
        min_samples_leaf=1,
        max_features=1.0,
        random_state=int(seed),
        n_jobs=-1,
    )
    model.fit(train_matrix, [row.reward for row in splits["train"]])
    feature_ranges, category_values = _feature_metadata(splits["train"])
    dataset_hash = _dataset_hash(rows)
    manifest: dict[str, Any] = {
        "schema_version": "khu-policy-model-manifest/v1",
        "model_type": MODEL_TYPE,
        "model_version": MODEL_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "dataset_db": str(Path(dataset_db).expanduser().resolve()),
        "dataset_sha256": dataset_hash,
        "seed": int(seed),
        "trees": int(trees),
        "split": {key: _split_summary(value) for key, value in splits.items()},
        "feature_ranges": feature_ranges,
        "category_values": category_values,
        "guard": {"minimum_confidence": 0.70, "maximum_ood_ratio": 0.20},
    }
    policy_model = PolicyModel(vectorizer, model, manifest)
    validation = evaluate_rows(policy_model, splits["validation"])
    test = evaluate_rows(policy_model, splits["test"])
    manifest["validation_metrics"] = validation
    manifest["test_metrics"] = test
    destination = Path(artifact_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    model_path = destination / "khu-policy.joblib"
    joblib.dump({"vectorizer": vectorizer, "model": model}, model_path)
    manifest["artifact_sha256"] = sha256(model_path.read_bytes()).hexdigest()
    manifest_path = destination / "khu-policy.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "artifact": str(model_path),
        "manifest": str(manifest_path),
        "validation_metrics": validation,
        "test_metrics": test,
        "sample_count": len(rows),
    }


def load_policy(artifact_dir: str | Path) -> PolicyModel:
    root = Path(artifact_dir).expanduser().resolve()
    model_path = root / "khu-policy.joblib"
    manifest_path = root / "khu-policy.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256(model_path.read_bytes()).hexdigest() != manifest.get("artifact_sha256"):
        raise ValueError("model artifact SHA-256 does not match manifest")
    payload = joblib.load(model_path)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("vectorizer"), DictVectorizer) or not isinstance(payload.get("model"), RandomForestRegressor):
        raise ValueError("model artifact has an unsupported structure")
    return PolicyModel(payload["vectorizer"], payload["model"], manifest)


def load_rows(dataset_db: str | Path) -> list[TrainingRow]:
    path = Path(dataset_db).expanduser().resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result = connection.execute(
            """
            SELECT scenario_id, source_scenario_id, candidate_id, action, feature_json,
                   reward, feasible, slo_satisfied, is_oracle
              FROM action_outcomes ORDER BY scenario_id, candidate_id, action
            """
        ).fetchall()
    finally:
        connection.close()
    rows: list[TrainingRow] = []
    for row in result:
        feature = json.loads(str(row["feature_json"]))
        if not isinstance(feature, dict):
            raise ValueError("feature_json must contain an object")
        rows.append(
            TrainingRow(
                scenario_id=str(row["scenario_id"]),
                source_scenario_id=str(row["source_scenario_id"]),
                candidate_id=str(row["candidate_id"]),
                action=str(row["action"]),
                features=dict(feature),
                reward=float(row["reward"]),
                feasible=bool(row["feasible"]),
                slo_satisfied=bool(row["slo_satisfied"]),
                is_oracle=bool(row["is_oracle"]),
            )
        )
    return rows


def evaluate_rows(policy: PolicyModel, rows: Sequence[TrainingRow]) -> dict[str, float]:
    if not rows:
        return {"samples": 0.0, "groups": 0.0}
    # Holdout evaluation can contain tens of thousands of candidate-action
    # rows. Transform and score them in one batch rather than calling each
    # individual tree once per row (online service requests stay small).
    matrix = policy.vectorizer.transform([row.features for row in rows])
    predictions = [float(value) for value in policy.model.predict(matrix)]
    mae = mean(abs(prediction - row.reward) for prediction, row in zip(predictions, rows))
    groups: dict[str, list[tuple[TrainingRow, float]]] = defaultdict(list)
    for row, prediction in zip(rows, predictions):
        groups[row.scenario_id].append((row, prediction))
    regret: list[float] = []
    infeasible = 0
    slo_violations = 0
    selected_rows: list[TrainingRow] = []
    truth: list[str] = []
    chosen: list[str] = []
    for group in groups.values():
        valid = [(row, score) for row, score in group if row.feasible]
        if not valid:
            continue
        selected, score = max(valid, key=lambda item: (item[1], item[0].candidate_id, item[0].action))
        oracle = max(group, key=lambda item: item[0].reward)[0]
        regret.append(max(0.0, oracle.reward - selected.reward))
        infeasible += int(not selected.feasible)
        slo_violations += int(not selected.slo_satisfied)
        selected_rows.append(selected)
        truth.append(oracle.action)
        chosen.append(selected.action)
    abstentions = _batch_abstentions(policy, selected_rows)
    macro_f1 = _macro_f1(truth, chosen)
    baseline = _baseline_metrics(groups)
    return {
        "samples": float(len(rows)),
        "groups": float(len(groups)),
        "mae": round(mae, 8),
        "mean_regret": round(mean(regret) if regret else 0.0, 8),
        "action_macro_f1": round(macro_f1, 8),
        "slo_violation_rate": round(slo_violations / max(1, len(groups)), 8),
        "infeasible_selection_rate": round(infeasible / max(1, len(groups)), 8),
        "ood_abstain_rate": round(abstentions / max(1, len(groups)), 8),
        **baseline,
    }


def _batch_abstentions(policy: PolicyModel, rows: Sequence[TrainingRow]) -> int:
    if not rows:
        return 0
    matrix = policy.vectorizer.transform([row.features for row in rows])
    expected = [float(value) for value in policy.model.predict(matrix)]
    per_tree = [tree.predict(matrix) for tree in policy.model.estimators_]
    result = 0
    for index, row in enumerate(rows):
        variance = sum((float(values[index]) - expected[index]) ** 2 for values in per_tree) / max(1, len(per_tree))
        spread = sqrt(variance)
        ood_ratio, _ = _ood(row.features, policy.manifest)
        confidence = max(0.0, min(1.0, 0.95 * (1.0 - min(1.0, spread / 0.50)) * (1.0 - ood_ratio)))
        result += int(confidence < 0.70 or ood_ratio > 0.20)
    return result


def _group_split(rows: Sequence[TrainingRow], seed: int) -> dict[str, list[TrainingRow]]:
    sources = sorted({row.source_scenario_id for row in rows})
    if len(sources) < 10:
        raise ValueError("at least 10 independent source scenarios are required")
    random.Random(seed).shuffle(sources)
    train_end = max(1, int(len(sources) * 0.70))
    validation_end = max(train_end + 1, int(len(sources) * 0.85))
    assignments = {
        "train": set(sources[:train_end]),
        "validation": set(sources[train_end:validation_end]),
        "test": set(sources[validation_end:]),
    }
    return {name: [row for row in rows if row.source_scenario_id in ids] for name, ids in assignments.items()}


def _feature_metadata(rows: Sequence[TrainingRow]) -> tuple[dict[str, list[float]], dict[str, list[str]]]:
    numeric: dict[str, list[float]] = {}
    categories: dict[str, set[str]] = {}
    for row in rows:
        for name, value in row.features.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                current = numeric.setdefault(name, [float(value), float(value)])
                current[0] = min(current[0], float(value))
                current[1] = max(current[1], float(value))
            else:
                categories.setdefault(name, set()).add(str(value))
    return ({key: value for key, value in sorted(numeric.items())}, {key: sorted(value) for key, value in sorted(categories.items())})


def _ood(features: Mapping[str, float | str], manifest: Mapping[str, Any]) -> tuple[float, list[str]]:
    ranges = manifest.get("feature_ranges", {})
    categories = manifest.get("category_values", {})
    checked = 0
    outside = 0
    warnings: list[str] = []
    for name, bound in ranges.items():
        if name not in features:
            continue
        checked += 1
        value = float(features[name])
        low, high = float(bound[0]), float(bound[1])
        if value < low or value > high:
            outside += 1
            warnings.append(f"feature_out_of_range:{name}")
    for name, allowed in categories.items():
        if name not in features:
            continue
        checked += 1
        if str(features[name]) not in allowed:
            outside += 1
            warnings.append(f"category_unseen:{name}")
    return (outside / checked if checked else 0.0, warnings)


def _dataset_hash(rows: Sequence[TrainingRow]) -> str:
    records = [
        {
            "source": row.source_scenario_id,
            "scenario": row.scenario_id,
            "candidate": row.candidate_id,
            "action": row.action,
            "reward": row.reward,
            "features": row.features,
        }
        for row in rows
    ]
    return sha256(json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _split_summary(rows: Sequence[TrainingRow]) -> dict[str, int]:
    return {"samples": len(rows), "source_scenarios": len({row.source_scenario_id for row in rows}), "decision_scenarios": len({row.scenario_id for row in rows})}


def _macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> float:
    labels = sorted(set(truth) | set(predicted))
    if not labels:
        return 0.0
    values = []
    for label in labels:
        tp = sum(1 for actual, chosen in zip(truth, predicted) if actual == label and chosen == label)
        fp = sum(1 for actual, chosen in zip(truth, predicted) if actual != label and chosen == label)
        fn = sum(1 for actual, chosen in zip(truth, predicted) if actual == label and chosen != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return mean(values)


def _baseline_metrics(groups: Mapping[str, Sequence[tuple[TrainingRow, float]]]) -> dict[str, float]:
    regrets: list[float] = []
    for rows in groups.values():
        feasible = [row for row, _ in rows if row.feasible]
        if not feasible:
            continue
        state = str(feasible[0].features.get("lifecycle_state", "NEW"))
        expected_action = {"NEW": "DEPLOY", "SPEC_DRIFT": "UPDATE", "SLO_PRESSURE": "SCALE_OUT", "UNDERUTILIZED": "SCALE_IN", "HEALTHY": "KEEP"}.get(state, "KEEP")
        candidates = [row for row in feasible if row.action == expected_action] or feasible
        selected = max(candidates, key=lambda row: (float(row.features.get("candidate_availability", 0.0)) - 0.02 * float(row.features.get("candidate_cost_per_hour", 0.0)), row.candidate_id))
        oracle = max((row for row, _ in rows), key=lambda row: row.reward)
        regrets.append(max(0.0, oracle.reward - selected.reward))
    return {"rule_baseline_mean_regret": round(mean(regrets) if regrets else 0.0, 8)}
