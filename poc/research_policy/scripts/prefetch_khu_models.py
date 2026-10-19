#!/usr/bin/env python3
"""Prefetch exactly the Hugging Face models selected by a live collection plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from khu_policy_agent.live_collector import (
    CPU_SCALING_PROFILES,
    DEFAULT_PROFILES,
    DIVERSE_PROFILES,
    LOCAL_EXHAUSTIVE_PROFILES,
    LOCAL_NOVEL_PROFILES,
    LOCAL_SPACE_FILLING_PROFILES,
    REMOTE_CPU_SCALING_PROFILES,
    REMOTE_DIVERSE_PROFILES,
    REMOTE_MODEL_DIVERSE_PROFILES,
    REMOTE_NOVEL_PROFILES,
    REMOTE_SPACE_FILLING_PROFILES,
    _available_cpu_indices,
    _available_gpu_indices,
    _plans_for,
    remote_exhaustive_profiles,
)


PROFILE_SETS = {
    "baseline": lambda shard: DEFAULT_PROFILES,
    "diverse": lambda shard: DIVERSE_PROFILES,
    "local-novel": lambda shard: LOCAL_NOVEL_PROFILES,
    "local-space-filling": lambda shard: LOCAL_SPACE_FILLING_PROFILES,
    "local-exhaustive": lambda shard: LOCAL_EXHAUSTIVE_PROFILES,
    "cpu-scaling": lambda shard: CPU_SCALING_PROFILES,
    "remote-cpu-scaling": lambda shard: REMOTE_CPU_SCALING_PROFILES,
    "remote-diverse": lambda shard: REMOTE_DIVERSE_PROFILES,
    "remote-novel": lambda shard: REMOTE_NOVEL_PROFILES,
    "remote-space-filling": lambda shard: REMOTE_SPACE_FILLING_PROFILES,
    "remote-exhaustive": remote_exhaustive_profiles,
    "remote-model-diverse": lambda shard: REMOTE_MODEL_DIVERSE_PROFILES,
}


def _positive_csv(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    result = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    if any(item <= 0 for item in result):
        raise ValueError("CPU core counts must be positive")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--device", choices=("cpu", "gpu", "both"), default="cpu")
    parser.add_argument("--profile-set", choices=tuple(PROFILE_SETS), default="baseline")
    parser.add_argument("--remote-grid-shard", type=int, default=0)
    parser.add_argument("--max-cases", type=int, default=1)
    parser.add_argument("--cpu-core-counts", default="")
    parser.add_argument("--cpu-core-samples-per-profile", type=int)
    parser.add_argument("--apply-cpu-core-counts-to-gpu", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    if args.max_cases <= 0:
        raise ValueError("max-cases must be positive")
    if args.remote_grid_shard < 0:
        raise ValueError("remote-grid-shard must be non-negative")

    devices = ("cpu", "cuda") if args.device == "both" else (("cuda",) if args.device == "gpu" else ("cpu",))
    profiles = PROFILE_SETS[args.profile_set](args.remote_grid_shard)
    plans, skipped = _plans_for(
        profiles,
        devices,
        _available_gpu_indices(),
        available_cpu_indices=_available_cpu_indices(),
        cpu_core_counts=_positive_csv(args.cpu_core_counts),
        cpu_core_samples_per_profile=args.cpu_core_samples_per_profile,
        apply_cpu_core_counts_to_gpu=args.apply_cpu_core_counts_to_gpu,
    )
    selected = plans[: args.max_cases]
    if not selected:
        raise RuntimeError("no executable collection plan is available on this VM")

    models: dict[tuple[str, str], dict[str, str]] = {}
    for plan in selected:
        key = (plan.profile.model_id, plan.profile.revision)
        models[key] = {"model_id": key[0], "revision": key[1]}

    payload = {
        "device": args.device,
        "profile_set": args.profile_set,
        "remote_grid_shard": args.remote_grid_shard,
        "max_cases": args.max_cases,
        "selected_plan_count": len(selected),
        "skipped": skipped,
        "models": list(models.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.plan_only:
        return

    from huggingface_hub import snapshot_download

    cache_dir = Path(args.cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    for model in models.values():
        snapshot_download(
            repo_id=model["model_id"],
            revision=model["revision"],
            cache_dir=str(cache_dir),
        )


if __name__ == "__main__":
    main()
