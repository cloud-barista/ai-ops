#!/usr/bin/env python3
"""Plan, review, run, and verify a bounded CB-Tumblebug data campaign."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from khu_policy_agent.campaign_accounting import (  # noqa: E402
    CampaignTotals,
    RunEvidence,
    RunValidationError,
    inspect_run,
    summarize_runs,
)
from khu_policy_agent.campaign_plan import (  # noqa: E402
    CampaignEntry,
    CampaignManifest,
    CampaignManifestError,
    CampaignState,
    load_manifest,
    remaining_entries,
)


class CampaignError(RuntimeError):
    """Raised when the campaign cannot safely continue."""


class CollectorError(CampaignError):
    """Raised when the underlying collector exits unsuccessfully."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int,
        diagnostic_directory: Path,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.diagnostic_directory = diagnostic_directory


STATE_NAME = "campaign-state.json"
SUMMARY_NAME = "campaign-summary.json"
_PRICE = re.compile(r"\$?([0-9]+(?:\.[0-9]+)?)(?:/hour)?")


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "completed_entries": [],
        "reviews": {},
        "estimated_cost_usd": 0.0,
        "failure": None,
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CampaignError(f"cannot read JSON file {path}: {error}") from error


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_state(campaign_root: Path) -> dict[str, Any]:
    path = campaign_root / STATE_NAME
    if not path.exists():
        return _empty_state()
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise CampaignError(f"unsupported campaign state: {path}")
    if not isinstance(payload.get("completed_entries"), list):
        raise CampaignError("campaign state completed_entries must be an array")
    if not isinstance(payload.get("reviews"), dict):
        raise CampaignError("campaign state reviews must be an object")
    return payload


def _completed_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for item in state["completed_entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("entry_id"), str):
            raise CampaignError("campaign state contains an invalid completed entry")
        result.append(item["entry_id"])
    if len(result) != len(set(result)):
        raise CampaignError("campaign state contains duplicate completed entries")
    return tuple(result)


def _evidence_from_state(state: Mapping[str, Any]) -> tuple[RunEvidence, ...]:
    fields = set(RunEvidence.__dataclass_fields__)
    result: list[RunEvidence] = []
    for item in state["completed_entries"]:
        raw = item.get("evidence")
        if not isinstance(raw, dict) or set(raw) != fields:
            raise CampaignError(
                f"completed entry {item.get('entry_id')} has invalid evidence"
            )
        try:
            result.append(RunEvidence(**raw))
        except TypeError as error:
            raise CampaignError(f"invalid stored run evidence: {error}") from error
    return tuple(result)


def _byte_goal_satisfied(manifest: CampaignManifest, totals: CampaignTotals) -> bool:
    return (
        totals.data_bytes >= manifest.target_bytes
        and totals.cpu_bytes >= manifest.cpu_minimum_bytes
        and totals.gpu_bytes >= manifest.gpu_minimum_bytes
    )


def _tier_coverage(
    manifest: CampaignManifest,
    evidence: Sequence[RunEvidence],
) -> dict[str, dict[str, Any]]:
    tiers = sorted({(entry.device, entry.spec_id) for entry in manifest.entries})
    result: dict[str, dict[str, Any]] = {}
    for device, spec_id in tiers:
        count = sum(
            1
            for item in evidence
            if item.device == device and item.spec_id == spec_id
        )
        key = f"{device}:{spec_id}"
        result[key] = {
            "device": device,
            "spec_id": spec_id,
            "successful_runs": count,
            "required_runs": manifest.minimum_successful_runs_per_tier,
            "satisfied": count >= manifest.minimum_successful_runs_per_tier,
        }
    return result


def _campaign_goal_satisfied(
    manifest: CampaignManifest,
    totals: CampaignTotals,
    evidence: Sequence[RunEvidence],
) -> bool:
    return _byte_goal_satisfied(manifest, totals) and all(
        item["satisfied"] for item in _tier_coverage(manifest, evidence).values()
    )


def _summary_payload(
    manifest: CampaignManifest,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _evidence_from_state(state)
    totals = summarize_runs(evidence)
    completed_ids = _completed_ids(state)
    remaining = remaining_entries(
        manifest, CampaignState(completed_entry_ids=completed_ids)
    )
    return {
        "schema_version": "1.0",
        "goal_satisfied": _campaign_goal_satisfied(manifest, totals, evidence),
        "target_bytes": manifest.target_bytes,
        "hard_ceiling_bytes": manifest.hard_ceiling_bytes,
        "cpu_minimum_bytes": manifest.cpu_minimum_bytes,
        "gpu_minimum_bytes": manifest.gpu_minimum_bytes,
        "totals": asdict(totals),
        "tier_coverage": _tier_coverage(manifest, evidence),
        "completed_entry_count": len(completed_ids),
        "remaining_entry_count": len(remaining),
        "estimated_cost_usd": round(float(state.get("estimated_cost_usd", 0.0)), 6),
        "failure": state.get("failure"),
    }


def _save_state_and_summary(
    campaign_root: Path,
    manifest: CampaignManifest,
    state: dict[str, Any],
) -> dict[str, Any]:
    _write_json(campaign_root / STATE_NAME, state)
    summary = _summary_payload(manifest, state)
    _write_json(campaign_root / SUMMARY_NAME, summary)
    return summary


def _entry_environment(entry: CampaignEntry, output_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "KHU_LOCAL_OUTPUT_DIR": str(output_dir),
            "KHU_RUN_ID": entry.entry_id,
            "KHU_TB_INFRA_ID": entry.entry_id,
            "KHU_TB_SPEC_ID": entry.spec_id,
            "KHU_TB_IMAGE_ID": entry.image_id,
            "KHU_TB_ZONE": entry.zone,
            "KHU_DEVICE_SCOPE": entry.device,
            "KHU_PROFILE_SET": entry.profile_set,
            "KHU_MAX_CASES": str(entry.max_cases),
            "KHU_CPU_CORE_COUNTS": ",".join(
                str(value) for value in entry.cpu_core_counts
            ),
            "KHU_CPU_CORE_SAMPLES_PER_PROFILE": (
                ""
                if entry.cpu_core_samples_per_profile is None
                else str(entry.cpu_core_samples_per_profile)
            ),
            "KHU_REMOTE_GRID_SHARD": str(entry.remote_grid_shard),
            "KHU_SAMPLE_INTERVAL_SECONDS": str(entry.sample_interval_seconds),
            "KHU_SEED": str(entry.seed),
            "KHU_TB_AUTO_DESTROY": "1",
            "KHU_TB_DESTROY_ON_FAILURE": "1",
        }
    )
    return environment


def _collector_command(collector: Path, action: str) -> list[str]:
    if collector.suffix.lower() == ".py":
        return [sys.executable, str(collector), action]
    return ["bash", str(collector), action]


def _invoke_collector(
    collector: Path,
    action: str,
    entry: CampaignEntry,
    output_dir: Path,
) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        _collector_command(collector, action),
        cwd=ROOT,
        env=_entry_environment(entry, output_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    (output_dir / f"controller-{action}.stdout.log").write_text(
        result.stdout, encoding="utf-8"
    )
    (output_dir / f"controller-{action}.stderr.log").write_text(
        result.stderr, encoding="utf-8"
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no error output"
        raise CollectorError(
            f"collector {action} failed for {entry.entry_id} "
            f"with exit code {result.returncode}: {detail}",
            exit_code=result.returncode,
            diagnostic_directory=output_dir,
        )
    return result


def _parse_hourly_cost(payload: Mapping[str, Any]) -> float:
    raw = payload.get("estimatedCost")
    if not isinstance(raw, (str, int, float)):
        raise CampaignError("review response has no estimatedCost")
    match = _PRICE.fullmatch(str(raw).strip())
    if not match:
        raise CampaignError(f"cannot parse review estimatedCost: {raw}")
    value = float(match.group(1))
    if value <= 0:
        raise CampaignError("review estimatedCost must be positive")
    return value


def _review_entry(
    collector: Path,
    entry: CampaignEntry,
    output_dir: Path,
) -> dict[str, Any]:
    _invoke_collector(collector, "review", entry, output_dir)
    response_path = output_dir / "review-response.json"
    payload = _read_json(response_path)
    if not isinstance(payload, dict):
        raise CampaignError(f"review response is not an object: {response_path}")
    if payload.get("creationViable") is not True:
        raise CampaignError(f"CB-Tumblebug rejected entry {entry.entry_id}")
    if str(payload.get("overallStatus", "")).lower() == "error":
        raise CampaignError(f"CB-Tumblebug review reported an error: {entry.entry_id}")
    return {
        "entry_id": entry.entry_id,
        "reviewed": True,
        "hourly_cost_usd": _parse_hourly_cost(payload),
        "response_path": str(response_path),
    }


def _run_paths(output_dir: Path, entry_id: str) -> tuple[Path, Path, Path]:
    return (
        output_dir / "extracted" / entry_id,
        output_dir / f"{entry_id}.tar.gz",
        output_dir / f"{entry_id}.tar.gz.sha256",
    )


def _record_failure(
    campaign_root: Path,
    manifest: CampaignManifest,
    state: dict[str, Any],
    entry_id: str | None,
    error: Exception,
) -> None:
    state["failure"] = {
        "entry_id": entry_id,
        "message": str(error),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(error, CollectorError):
        state["failure"].update(
            {
                "exit_code": error.exit_code,
                "diagnostic_directory": str(error.diagnostic_directory),
            }
        )
    _save_state_and_summary(campaign_root, manifest, state)


def _ensure_free_space(campaign_root: Path, manifest: CampaignManifest) -> None:
    campaign_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(campaign_root).free
    required = (
        manifest.minimum_free_bytes
        + manifest.hard_ceiling_bytes
        + 512 * 1024 * 1024
    )
    if free < required:
        raise CampaignError(
            f"insufficient disk space: free={free}, required={required}"
        )


def _assert_auto_destroy_environment() -> None:
    for name in ("KHU_TB_AUTO_DESTROY", "KHU_TB_DESTROY_ON_FAILURE"):
        inherited = os.environ.get(name)
        if inherited is not None and inherited != "1":
            raise CampaignError(
                f"{name} conflicts with mandatory live value 1: {inherited}"
            )


def _predicted_next_bytes(
    state: Mapping[str, Any], entry: CampaignEntry
) -> int | None:
    evidence = _evidence_from_state(state)
    same = [item.data_bytes for item in evidence if item.device == entry.device]
    if same:
        return max(same)
    if evidence:
        return max(item.data_bytes for item in evidence)
    return None


def _ensure_next_run_fits(
    manifest: CampaignManifest,
    state: Mapping[str, Any],
    entry: CampaignEntry,
) -> None:
    totals = summarize_runs(_evidence_from_state(state))
    quota_floor = (
        totals.data_bytes
        + max(0, manifest.cpu_minimum_bytes - totals.cpu_bytes)
        + max(0, manifest.gpu_minimum_bytes - totals.gpu_bytes)
    )
    if quota_floor > manifest.hard_ceiling_bytes:
        raise CampaignError(
            "hard data ceiling cannot accommodate the remaining CPU/GPU quotas: "
            f"{quota_floor} > {manifest.hard_ceiling_bytes}"
        )
    estimate = _predicted_next_bytes(state, entry)
    if estimate is not None and totals.data_bytes + estimate > manifest.hard_ceiling_bytes:
        raise CampaignError(
            f"hard data ceiling would be exceeded by predicted entry {entry.entry_id}: "
            f"{totals.data_bytes + estimate} > {manifest.hard_ceiling_bytes}"
        )


def _plan(
    manifest: CampaignManifest,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    summary = _summary_payload(manifest, state)
    completed_ids = _completed_ids(state)
    remaining = remaining_entries(
        manifest, CampaignState(completed_entry_ids=completed_ids)
    )
    worst_case = sum(
        entry.hourly_cost_usd * entry.max_runtime_minutes / 60 for entry in remaining
    )
    return {
        **summary,
        "live_cloud_execution": False,
        "worst_case_remaining_cost_usd": round(worst_case, 6),
        "remaining_entries": [entry.entry_id for entry in remaining],
    }


def _review(
    manifest: CampaignManifest,
    state: dict[str, Any],
    campaign_root: Path,
    collector: Path,
) -> dict[str, Any]:
    entries = remaining_entries(
        manifest,
        CampaignState(completed_entry_ids=_completed_ids(state)),
    )
    for entry in entries:
        output_dir = campaign_root / "runs" / entry.entry_id
        state["reviews"][entry.entry_id] = _review_entry(
            collector, entry, output_dir
        )
        state["failure"] = None
        _save_state_and_summary(campaign_root, manifest, state)
    return _summary_payload(manifest, state)


def _estimated_entry_cost(entry: CampaignEntry, review: Mapping[str, Any]) -> float:
    hourly = float(review["hourly_cost_usd"])
    return hourly * entry.max_runtime_minutes / 60


def _run(
    manifest: CampaignManifest,
    state: dict[str, Any],
    campaign_root: Path,
    collector: Path,
    max_cost_usd: float,
    max_live_entries: int | None,
) -> dict[str, Any]:
    _ensure_free_space(campaign_root, manifest)
    entries = remaining_entries(
        manifest,
        CampaignState(completed_entry_ids=_completed_ids(state)),
    )
    all_remaining_count = len(entries)
    if max_live_entries is not None:
        entries = entries[:max_live_entries]
    existing_evidence = _evidence_from_state(state)
    if _campaign_goal_satisfied(
        manifest, summarize_runs(existing_evidence), existing_evidence
    ):
        return _save_state_and_summary(campaign_root, manifest, state)

    for entry in entries:
        output_dir = campaign_root / "runs" / entry.entry_id
        try:
            review = _review_entry(collector, entry, output_dir)
            state["reviews"][entry.entry_id] = review
            entry_cost = _estimated_entry_cost(entry, review)
            estimated_cost = float(state.get("estimated_cost_usd", 0.0))
            if estimated_cost + entry_cost > max_cost_usd:
                raise CampaignError(
                    f"cost ceiling would be exceeded by {entry.entry_id}: "
                    f"{estimated_cost + entry_cost:.4f} > {max_cost_usd:.4f} USD"
                )
            _ensure_next_run_fits(manifest, state, entry)
            # Reserve the reviewed maximum before provisioning. A failed attempt can
            # still incur cloud cost, so retries must not silently reset the budget.
            state["estimated_cost_usd"] = round(estimated_cost + entry_cost, 6)
            _save_state_and_summary(campaign_root, manifest, state)
            _invoke_collector(collector, "run", entry, output_dir)
            run_dir, archive, checksum = _run_paths(output_dir, entry.entry_id)
            evidence = inspect_run(run_dir, archive, checksum)
            if evidence.run_id != entry.entry_id:
                raise CampaignError(
                    f"run id mismatch: expected {entry.entry_id}, got {evidence.run_id}"
                )
            if (
                evidence.infra_id != entry.entry_id
                or evidence.spec_id != entry.spec_id
                or evidence.image_id != entry.image_id
                or evidence.device != entry.device
                or evidence.profile_set != entry.profile_set
                or evidence.seed != entry.seed
            ):
                raise CampaignError(f"run provenance mismatch: {entry.entry_id}")
            prospective = summarize_runs((*_evidence_from_state(state), evidence))
            if prospective.data_bytes > manifest.hard_ceiling_bytes:
                raise CampaignError(
                    f"hard data ceiling exceeded after {entry.entry_id}: "
                    f"{prospective.data_bytes} > {manifest.hard_ceiling_bytes}"
                )
            state["completed_entries"].append(
                {
                    "entry_id": entry.entry_id,
                    "output_dir": str(output_dir),
                    "evidence": asdict(evidence),
                    "estimated_cost_usd": round(entry_cost, 6),
                }
            )
            state["failure"] = None
            summary = _save_state_and_summary(campaign_root, manifest, state)
            if summary["goal_satisfied"]:
                return summary
        except Exception as error:
            _record_failure(campaign_root, manifest, state, entry.entry_id, error)
            raise
    summary = _save_state_and_summary(campaign_root, manifest, state)
    if (
        max_live_entries is not None
        and len(entries) < all_remaining_count
        and not summary["goal_satisfied"]
    ):
        summary["live_entry_limit_reached"] = True
        _write_json(campaign_root / SUMMARY_NAME, summary)
        return summary
    if not summary["goal_satisfied"]:
        raise CampaignError("manifest entries were exhausted before campaign goal")
    return summary


def _verify(
    manifest: CampaignManifest,
    state: dict[str, Any],
    campaign_root: Path,
) -> dict[str, Any]:
    verified: list[RunEvidence] = []
    for item in state["completed_entries"]:
        entry_id = item["entry_id"]
        output_dir = Path(item["output_dir"])
        run_dir, archive, checksum = _run_paths(output_dir, entry_id)
        verified.append(inspect_run(run_dir, archive, checksum))
    totals = summarize_runs(verified)
    payload = {
        "verified_entry_count": len(verified),
        "goal_satisfied": _campaign_goal_satisfied(manifest, totals, verified),
        "totals": asdict(totals),
    }
    _write_json(campaign_root / "campaign-verification.json", payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "review", "run", "verify"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--execute-live-cloud", action="store_true")
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--max-live-entries", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign_root = args.campaign_root.resolve()
    collector = args.collector.resolve()
    try:
        manifest = load_manifest(args.manifest)
        if not collector.is_file():
            raise CampaignError(f"collector does not exist: {collector}")
        state = _load_state(campaign_root)
        if args.action == "plan":
            payload = _plan(manifest, state)
        elif args.action == "review":
            payload = _review(manifest, state, campaign_root, collector)
        elif args.action == "run":
            if not args.execute_live_cloud:
                raise CampaignError("run requires --execute-live-cloud")
            if args.max_cost_usd is None or args.max_cost_usd <= 0:
                raise CampaignError("run requires a positive --max-cost-usd")
            if args.max_live_entries is not None and args.max_live_entries <= 0:
                raise CampaignError("--max-live-entries must be positive")
            _assert_auto_destroy_environment()
            payload = _run(
                manifest,
                state,
                campaign_root,
                collector,
                args.max_cost_usd,
                args.max_live_entries,
            )
        else:
            payload = _verify(manifest, state, campaign_root)
    except (
        CampaignError,
        CampaignManifestError,
        RunValidationError,
        OSError,
        ValueError,
    ) as error:
        print(f"campaign error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
