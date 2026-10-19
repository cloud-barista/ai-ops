"""Strict manifest and deterministic planning types for a VM data campaign."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


GIB = 1024**3


class CampaignManifestError(ValueError):
    """Raised when a campaign manifest cannot be executed safely."""


@dataclass(frozen=True)
class CampaignEntry:
    entry_id: str
    spec_id: str
    image_id: str
    zone: str
    device: str
    profile_set: str
    max_cases: int
    cpu_core_counts: tuple[int, ...]
    cpu_core_samples_per_profile: int | None
    remote_grid_shard: int
    sample_interval_seconds: float
    seed: int
    hourly_cost_usd: float
    max_runtime_minutes: int


@dataclass(frozen=True)
class CampaignManifest:
    schema_version: str
    target_bytes: int
    hard_ceiling_bytes: int
    minimum_free_bytes: int
    cpu_minimum_bytes: int
    gpu_minimum_bytes: int
    minimum_successful_runs_per_tier: int
    entries: tuple[CampaignEntry, ...]


@dataclass(frozen=True)
class CampaignState:
    completed_entry_ids: tuple[str, ...] = ()
    failed_entry_id: str | None = None


_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "target_bytes",
        "hard_ceiling_bytes",
        "minimum_free_bytes",
        "cpu_minimum_bytes",
        "gpu_minimum_bytes",
        "minimum_successful_runs_per_tier",
        "entries",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "entry_id",
        "spec_id",
        "image_id",
        "zone",
        "device",
        "profile_set",
        "max_cases",
        "cpu_core_counts",
        "cpu_core_samples_per_profile",
        "remote_grid_shard",
        "sample_interval_seconds",
        "seed",
        "hourly_cost_usd",
        "max_runtime_minutes",
    }
)
_PROFILE_SETS = frozenset(
    {
        "baseline",
        "diverse",
        "local-novel",
        "local-space-filling",
        "local-exhaustive",
        "cpu-scaling",
        "remote-cpu-scaling",
        "remote-diverse",
        "remote-novel",
        "remote-space-filling",
        "remote-exhaustive",
        "remote-model-diverse",
    }
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CampaignManifestError(f"{label} must be a JSON object")
    return value


def _integer(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CampaignManifestError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: object, label: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignManifestError(f"{label} must be numeric")
    result = float(value)
    if positive and result <= 0:
        raise CampaignManifestError(f"{label} must be positive")
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignManifestError(f"{label} must be a non-empty string")
    return value


def _entry(payload: object, index: int) -> CampaignEntry:
    item = _mapping(payload, f"entries[{index}]")
    unknown = sorted(set(item) - _ENTRY_KEYS)
    if unknown:
        raise CampaignManifestError(f"unknown entry key(s): {', '.join(unknown)}")
    missing = sorted(_ENTRY_KEYS - set(item))
    if missing:
        raise CampaignManifestError(f"missing entry key(s): {', '.join(missing)}")

    entry_id = _text(item["entry_id"], "entry_id")
    if not _SAFE_ID.fullmatch(entry_id):
        raise CampaignManifestError(f"entry_id is unsafe: {entry_id}")
    device = _text(item["device"], "device")
    if device not in {"cpu", "gpu"}:
        raise CampaignManifestError("device must be cpu or gpu")
    profile_set = _text(item["profile_set"], "profile_set")
    if profile_set not in _PROFILE_SETS:
        raise CampaignManifestError(f"unsupported profile_set: {profile_set}")

    core_values = item["cpu_core_counts"]
    if not isinstance(core_values, list):
        raise CampaignManifestError("cpu_core_counts must be a JSON array")
    cpu_core_counts = tuple(
        _integer(value, "cpu_core_counts item") for value in core_values
    )
    if len(set(cpu_core_counts)) != len(cpu_core_counts):
        raise CampaignManifestError("cpu_core_counts must not contain duplicates")
    if device == "cpu" and not cpu_core_counts:
        raise CampaignManifestError("CPU entry requires cpu_core_counts")

    sample_count_value = item["cpu_core_samples_per_profile"]
    if sample_count_value is None:
        cpu_core_samples_per_profile = None
    else:
        cpu_core_samples_per_profile = _integer(
            sample_count_value, "cpu_core_samples_per_profile"
        )

    remote_grid_shard = _integer(
        item["remote_grid_shard"], "remote_grid_shard", minimum=0
    )
    if remote_grid_shard and profile_set != "remote-exhaustive":
        raise CampaignManifestError(
            "remote_grid_shard is only valid for remote-exhaustive"
        )

    return CampaignEntry(
        entry_id=entry_id,
        spec_id=_text(item["spec_id"], "spec_id"),
        image_id=_text(item["image_id"], "image_id"),
        zone=_text(item["zone"], "zone"),
        device=device,
        profile_set=profile_set,
        max_cases=_integer(item["max_cases"], "max_cases"),
        cpu_core_counts=cpu_core_counts,
        cpu_core_samples_per_profile=cpu_core_samples_per_profile,
        remote_grid_shard=remote_grid_shard,
        sample_interval_seconds=_number(
            item["sample_interval_seconds"], "sample_interval_seconds"
        ),
        seed=_integer(item["seed"], "seed", minimum=0),
        hourly_cost_usd=_number(item["hourly_cost_usd"], "hourly_cost_usd"),
        max_runtime_minutes=_integer(
            item["max_runtime_minutes"], "max_runtime_minutes"
        ),
    )


def load_manifest(path: Path) -> CampaignManifest:
    """Load and validate one strict campaign manifest."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CampaignManifestError(f"cannot read campaign manifest: {error}") from error
    root = _mapping(payload, "manifest")
    unknown = sorted(set(root) - _MANIFEST_KEYS)
    if unknown:
        raise CampaignManifestError(f"unknown manifest key(s): {', '.join(unknown)}")
    missing = sorted(_MANIFEST_KEYS - set(root))
    if missing:
        raise CampaignManifestError(f"missing manifest key(s): {', '.join(missing)}")

    schema_version = _text(root["schema_version"], "schema_version")
    if schema_version != "1.0":
        raise CampaignManifestError(f"unsupported schema_version: {schema_version}")
    target_bytes = _integer(root["target_bytes"], "target_bytes")
    if target_bytes < GIB:
        raise CampaignManifestError("target_bytes must be at least 1 GiB")
    hard_ceiling_bytes = _integer(
        root["hard_ceiling_bytes"], "hard_ceiling_bytes"
    )
    if hard_ceiling_bytes < target_bytes:
        raise CampaignManifestError("hard ceiling must be >= target_bytes")
    minimum_free_bytes = _integer(root["minimum_free_bytes"], "minimum_free_bytes")
    cpu_minimum_bytes = _integer(
        root["cpu_minimum_bytes"], "cpu_minimum_bytes", minimum=0
    )
    gpu_minimum_bytes = _integer(
        root["gpu_minimum_bytes"], "gpu_minimum_bytes", minimum=0
    )
    minimum_successful_runs_per_tier = _integer(
        root["minimum_successful_runs_per_tier"],
        "minimum_successful_runs_per_tier",
    )
    if cpu_minimum_bytes <= 0 or gpu_minimum_bytes <= 0:
        raise CampaignManifestError("CPU and GPU quotas must both be positive")
    if cpu_minimum_bytes + gpu_minimum_bytes > hard_ceiling_bytes:
        raise CampaignManifestError("CPU and GPU quotas exceed the hard ceiling")

    raw_entries = root["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CampaignManifestError("entries must be a non-empty JSON array")
    entries = tuple(_entry(item, index) for index, item in enumerate(raw_entries))
    ids = [item.entry_id for item in entries]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise CampaignManifestError(f"duplicate entry_id: {', '.join(duplicates)}")
    devices = {item.device for item in entries}
    if devices != {"cpu", "gpu"}:
        raise CampaignManifestError("entries must include both CPU and GPU devices")

    return CampaignManifest(
        schema_version=schema_version,
        target_bytes=target_bytes,
        hard_ceiling_bytes=hard_ceiling_bytes,
        minimum_free_bytes=minimum_free_bytes,
        cpu_minimum_bytes=cpu_minimum_bytes,
        gpu_minimum_bytes=gpu_minimum_bytes,
        minimum_successful_runs_per_tier=minimum_successful_runs_per_tier,
        entries=entries,
    )


def remaining_entries(
    manifest: CampaignManifest, state: CampaignState
) -> tuple[CampaignEntry, ...]:
    """Return unfinished entries in their original deterministic order."""

    completed = frozenset(state.completed_entry_ids)
    unknown = sorted(completed - {entry.entry_id for entry in manifest.entries})
    if unknown:
        raise CampaignManifestError(
            f"state refers to unknown completed entries: {', '.join(unknown)}"
        )
    return tuple(entry for entry in manifest.entries if entry.entry_id not in completed)
