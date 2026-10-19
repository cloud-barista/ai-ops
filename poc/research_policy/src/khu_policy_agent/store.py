"""SQLite persistence for the synthetic KHU deployment benchmark."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from .contracts import DATASET_SCHEMA_VERSION


class KHUScenarioStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def __enter__(self) -> "KHUScenarioStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.connection.close()

    def begin_run(self, run_id: str, seed: int, source_db: str, config: Mapping[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO dataset_runs(run_id, schema_version, seed, source_db, started_at, config_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, DATASET_SCHEMA_VERSION, int(seed), source_db, _now(), _json(config)),
            )

    def add_scenario(self, record: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO deployment_scenarios(
                scenario_id, run_id, source_scenario_id, source_ordinal, lifecycle_state,
                application_json, operation_json, oracle_action, oracle_candidate_id,
                oracle_reward, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["scenario_id"], record["run_id"], record["source_scenario_id"],
                int(record["source_ordinal"]), record["lifecycle_state"],
                _json(record["application"]), _json(record["operation"]),
                record["oracle_action"], record["oracle_candidate_id"],
                float(record["oracle_reward"]), _now(),
            ),
        )

    def add_outcomes(self, outcomes: Iterable[Mapping[str, Any]]) -> int:
        count = 0
        for row in outcomes:
            self.connection.execute(
                """
                INSERT INTO action_outcomes(
                    outcome_id, scenario_id, source_scenario_id, candidate_id, action,
                    desired_replicas, feasible, success, slo_satisfied, is_oracle,
                    reward, cost_per_hour, latency_p95_ms, throughput_rps,
                    error_rate_percent, cpu_utilization_percent, memory_utilization_percent,
                    gpu_utilization_percent, feature_json, outcome_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["outcome_id"], row["scenario_id"], row["source_scenario_id"],
                    row["candidate_id"], row["action"], int(row["desired_replicas"]),
                    int(bool(row["feasible"])), int(bool(row["success"])),
                    int(bool(row["slo_satisfied"])), int(bool(row["is_oracle"])),
                    float(row["reward"]), float(row["cost_per_hour"]),
                    float(row["latency_p95_ms"]), float(row["throughput_rps"]),
                    float(row["error_rate_percent"]), float(row["cpu_utilization_percent"]),
                    float(row["memory_utilization_percent"]), float(row["gpu_utilization_percent"]),
                    _json(row["features"]), _json(row["outcome"]),
                ),
            )
            count += 1
        return count

    def finish_run(self, run_id: str) -> None:
        counts = self.connection.execute(
            "SELECT COUNT(*) AS scenarios FROM deployment_scenarios WHERE run_id = ?", (run_id,)
        ).fetchone()
        with self.connection:
            self.connection.execute(
                "UPDATE dataset_runs SET finished_at = ?, scenario_count = ? WHERE run_id = ?",
                (_now(), int(counts["scenarios"]), run_id),
            )

    def summary(self, run_id: str) -> dict[str, int]:
        row = self.connection.execute(
            """
            SELECT COUNT(DISTINCT ds.scenario_id) AS scenarios,
                   COUNT(ao.outcome_id) AS outcomes,
                   SUM(CASE WHEN ao.is_oracle = 1 THEN 1 ELSE 0 END) AS oracle_rows,
                   SUM(CASE WHEN ao.feasible = 1 THEN 1 ELSE 0 END) AS feasible_rows
              FROM deployment_scenarios ds
              LEFT JOIN action_outcomes ao ON ao.scenario_id = ds.scenario_id
             WHERE ds.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    def export_csv(self, destination: str | Path) -> dict[str, str]:
        directory = Path(destination).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        files = {
            "runs": directory / "dataset_runs.csv",
            "scenarios": directory / "deployment_scenarios.csv",
            "outcomes": directory / "action_outcomes.csv",
        }
        for name, query in {
            "runs": "SELECT * FROM dataset_runs ORDER BY started_at, run_id",
            "scenarios": "SELECT * FROM deployment_scenarios ORDER BY source_ordinal, lifecycle_state",
            "outcomes": "SELECT * FROM action_outcomes ORDER BY scenario_id, candidate_id, action",
        }.items():
            cursor = self.connection.execute(query)
            columns = [item[0] for item in cursor.description]
            with files[name].open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(cursor)
        return {key: str(value) for key, value in files.items()}

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS dataset_runs (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                seed INTEGER NOT NULL,
                source_db TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                scenario_count INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_scenarios (
                scenario_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES dataset_runs(run_id),
                source_scenario_id TEXT NOT NULL,
                source_ordinal INTEGER NOT NULL,
                lifecycle_state TEXT NOT NULL,
                application_json TEXT NOT NULL,
                operation_json TEXT NOT NULL,
                oracle_action TEXT NOT NULL,
                oracle_candidate_id TEXT NOT NULL,
                oracle_reward REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_outcomes (
                outcome_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL REFERENCES deployment_scenarios(scenario_id) ON DELETE CASCADE,
                source_scenario_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                action TEXT NOT NULL,
                desired_replicas INTEGER NOT NULL,
                feasible INTEGER NOT NULL,
                success INTEGER NOT NULL,
                slo_satisfied INTEGER NOT NULL,
                is_oracle INTEGER NOT NULL,
                reward REAL NOT NULL,
                cost_per_hour REAL NOT NULL,
                latency_p95_ms REAL NOT NULL,
                throughput_rps REAL NOT NULL,
                error_rate_percent REAL NOT NULL,
                cpu_utilization_percent REAL NOT NULL,
                memory_utilization_percent REAL NOT NULL,
                gpu_utilization_percent REAL NOT NULL,
                feature_json TEXT NOT NULL,
                outcome_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_khu_scenarios_source ON deployment_scenarios(source_scenario_id);
            CREATE INDEX IF NOT EXISTS idx_khu_outcomes_scenario ON action_outcomes(scenario_id, is_oracle);
            """
        )
        self.connection.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
