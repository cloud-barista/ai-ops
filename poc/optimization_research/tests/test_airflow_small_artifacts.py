"""Check the selected Airflow prefix can be audited without a DB restore."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_airflow_small_artifacts.py"
SPEC = importlib.util.spec_from_file_location("airflow_small_artifact_audit", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def test_prefix_distinguishes_header_from_complete_member() -> None:
    data_dir = Path(__file__).resolve().parents[1] / "data/external/airflow_action_feasibility"
    result = study.inspect(data_dir)
    prefix = result["db_prefix"]
    assert prefix["tar_member_headers_seen"] == 730
    assert prefix["complete_tar_members_seen"] == 729
    assert prefix["truncated_as_expected"] is True
    assert prefix["incomplete_member"].startswith("db/PlanningFirst_")
    assert len(prefix["selected_members_seen"]) == 5
    assert result["action_counts_or_matching_rates_measured"] is False
