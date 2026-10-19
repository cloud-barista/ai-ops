from __future__ import annotations

import json
from pathlib import Path

import pytest

from khu_policy_agent.campaign_plan import (
    CampaignManifestError,
    CampaignState,
    load_manifest,
    remaining_entries,
)


def _entry(entry_id: str = "cpu-m6a-large-baseline-r1", **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "entry_id": entry_id,
        "spec_id": "aws+us-west-2+m6a.large",
        "image_id": "ami-0eb3161272dc9c6eb",
        "zone": "us-west-2a",
        "device": "cpu",
        "profile_set": "baseline",
        "max_cases": 13,
        "cpu_core_counts": [2],
        "cpu_core_samples_per_profile": 1,
        "remote_grid_shard": 0,
        "sample_interval_seconds": 0.1,
        "seed": 42,
        "hourly_cost_usd": 0.0864,
        "max_runtime_minutes": 120,
    }
    item.update(overrides)
    return item


def _manifest(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "target_bytes": 1_288_490_189,
        "hard_ceiling_bytes": 1_610_612_736,
        "minimum_free_bytes": 5_368_709_120,
        "cpu_minimum_bytes": 419_430_400,
        "gpu_minimum_bytes": 734_003_200,
        "minimum_successful_runs_per_tier": 3,
        "entries": [
            _entry(),
            _entry(
                "gpu-g6-8xl-exhaustive-r1",
                spec_id="aws+us-west-2+g6.8xlarge",
                image_id="ami-039f004668b2adc12",
                device="gpu",
                profile_set="local-exhaustive",
                max_cases=576,
                cpu_core_counts=[],
                cpu_core_samples_per_profile=None,
                hourly_cost_usd=2.0144,
                max_runtime_minutes=180,
            ),
        ],
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_manifest_accepts_strict_campaign_schema(tmp_path: Path) -> None:
    manifest = load_manifest(_write(tmp_path / "campaign.json", _manifest()))

    assert manifest.target_bytes == 1_288_490_189
    assert manifest.hard_ceiling_bytes == 1_610_612_736
    assert manifest.cpu_minimum_bytes == 419_430_400
    assert manifest.gpu_minimum_bytes == 734_003_200
    assert manifest.minimum_successful_runs_per_tier == 3
    assert manifest.entries[0].entry_id == "cpu-m6a-large-baseline-r1"
    assert manifest.entries[1].device == "gpu"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_manifest(unexpected=True), "unknown manifest key"),
        (_manifest(target_bytes=1_000_000), "at least 1 GiB"),
        (_manifest(hard_ceiling_bytes=1_200_000_000), "hard ceiling"),
        (_manifest(cpu_minimum_bytes=0), "CPU and GPU quotas"),
        (
            _manifest(minimum_successful_runs_per_tier=0),
            "minimum_successful_runs_per_tier",
        ),
        (
            _manifest(entries=[_entry(), _entry()]),
            "duplicate entry_id",
        ),
        (
            _manifest(entries=[_entry(device="tpu")]),
            "device",
        ),
        (
            _manifest(entries=[_entry(hourly_cost_usd=0)]),
            "hourly_cost_usd",
        ),
        (
            _manifest(entries=[_entry(max_runtime_minutes=0)]),
            "max_runtime_minutes",
        ),
        (
            _manifest(entries=[_entry(extra="value")]),
            "unknown entry key",
        ),
    ],
)
def test_load_manifest_rejects_invalid_input(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    with pytest.raises(CampaignManifestError, match=message):
        load_manifest(_write(tmp_path / "campaign.json", payload))


def test_remaining_entries_preserves_manifest_order(tmp_path: Path) -> None:
    payload = _manifest(
        entries=[
            _entry("first"),
            _entry(
                "second",
                spec_id="aws+us-west-2+g6.8xlarge",
                image_id="ami-039f004668b2adc12",
                device="gpu",
                profile_set="local-exhaustive",
                max_cases=576,
                cpu_core_counts=[],
                cpu_core_samples_per_profile=None,
                hourly_cost_usd=2.0144,
            ),
            _entry("third"),
        ]
    )
    manifest = load_manifest(_write(tmp_path / "campaign.json", payload))
    state = CampaignState(completed_entry_ids=("first", "third"))

    remaining = remaining_entries(manifest, state)

    assert [entry.entry_id for entry in remaining] == ["second"]
