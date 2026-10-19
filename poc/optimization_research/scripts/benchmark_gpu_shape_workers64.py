"""Exact-output and paired timing study of ExtraTrees workers at 64 grids."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import assess_gpu_shape as shape
import predict_performance as predictor
from assess_gpu_shape_microbatch_v5 import assess_gpu_shape_microbatch_v5 as assess
from benchmark_gpu_shape_microbatch_v4 import (
    MODEL, SOURCE, SCENARIOS, canonical, ordered_requests, request_blocks,
    verify_frozen_inputs,
)
from train_performance import ROOT


PROTOCOL = ROOT / "reports/decision_inference_workers64_protocol.md"
OUT = ROOT / "artifacts/gpu_shape_workers64_v1/results.json"
WORKERS = (1, 2, 4)
REPETITIONS = 7
ORDERS = tuple(itertools.permutations(WORKERS))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assess_with_workers(bundle: dict, requests: list[dict], workers: int) -> list[dict]:
    if workers not in WORKERS:
        raise ValueError("Unsupported worker count")

    def configure(active: dict) -> dict:
        if active.get("selected") == "extra_trees":
            active["model"].named_steps["regressor"].set_params(n_jobs=workers)
        return active

    before = bundle["model"].named_steps["regressor"].n_jobs
    with patch.object(predictor, "configure_predict_workers", side_effect=configure):
        result = assess(bundle, requests, max_grids=64)
    if bundle["model"].named_steps["regressor"].n_jobs != before:
        raise AssertionError("Shared fitted regressor was mutated")
    return result


def memory_worker(scenario: str, workers: int) -> dict:
    manifest = verify_frozen_inputs()
    requests = ordered_requests(request_blocks(scenario, manifest), None)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    output = assess_with_workers(bundle, requests, workers)
    return {
        "scenario": scenario,
        "workers": workers,
        "outputs": len(output),
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
    }


def run_memory_worker(scenario: str, workers: int) -> dict:
    proc = subprocess.run([
        sys.executable, str(Path(__file__).resolve()), "--memory-worker", scenario,
        "--workers", str(workers),
    ], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def output_differences(reference: list[dict], candidate: list[dict]) -> dict:
    """Describe exact-output drift without treating tiny values as identical."""
    numeric = 0
    other = 0
    maximum_absolute = 0.0
    maximum_relative = 0.0

    def walk(left, right) -> None:
        nonlocal numeric, other, maximum_absolute, maximum_relative
        if type(left) is not type(right):
            other += 1
        elif isinstance(left, dict):
            if left.keys() != right.keys():
                other += 1
            else:
                for key in left:
                    walk(left[key], right[key])
        elif isinstance(left, list):
            if len(left) != len(right):
                other += 1
            else:
                for a, b in zip(left, right):
                    walk(a, b)
        elif left != right:
            if type(left) in (int, float):
                numeric += 1
                maximum_absolute = max(maximum_absolute, abs(left - right))
                maximum_relative = max(
                    maximum_relative, abs(left - right) / max(abs(left), 1e-300))
            else:
                other += 1

    if len(reference) != len(candidate):
        other += 1
    else:
        for left, right in zip(reference, candidate):
            walk(left, right)

    def shape_key(item: dict):
        selected = item.get("selected")
        return (selected.get("gpu_replicas"), selected.get("cpu_affinity_cores")) if selected else None

    return {
        "numeric_fields_changed": numeric,
        "other_fields_changed": other,
        "maximum_absolute_numeric_difference": maximum_absolute,
        "maximum_relative_numeric_difference": maximum_relative,
        "decision_status_changed": sum(a.get("status") != b.get("status")
                                       for a, b in zip(reference, candidate)),
        "selected_shape_changed": sum(shape_key(a) != shape_key(b)
                                      for a, b in zip(reference, candidate)),
    }


def benchmark_scenario(scenario: str, manifest: dict, bundle: dict) -> dict:
    blocks = request_blocks(scenario, manifest)
    initial_requests = ordered_requests(blocks, None)
    initial = {worker: assess_with_workers(bundle, initial_requests, worker)
               for worker in WORKERS}
    bytes_by_worker = {worker: canonical(initial[worker]) for worker in WORKERS}
    hashes = {str(worker): hashlib.sha256(bytes_by_worker[worker]).hexdigest()
              for worker in WORKERS}
    exact = {str(worker): bytes_by_worker[worker] == bytes_by_worker[1]
             for worker in WORKERS}
    result = {
        "scenario": scenario,
        "requests": len(initial_requests),
        "initial_canonical_result_sha256": hashes,
        "initial_exact_json_equal_to_worker1": exact,
        "difference_from_worker1": {
            str(worker): output_differences(initial[1], initial[worker])
            for worker in (2, 4)
        },
    }
    if not all(exact.values()):
        return result
    # Warm each complete evaluation path. Model/source loading and this pass
    # are deliberately outside the paired timer.
    for worker in WORKERS:
        assess_with_workers(bundle, initial_requests, worker)

    timings = {str(worker): {"wall_ms": [], "cpu_ms": []} for worker in WORKERS}
    pair_ratios = {str(worker): [] for worker in (2, 4)}
    all_pair_equal = True
    orders = []
    for repetition in range(REPETITIONS):
        active = ordered_requests(blocks, repetition)
        order = ORDERS[repetition % len(ORDERS)]
        orders.append(order)
        outputs = {}
        walls = {}
        for worker in order:
            wall_start = time.perf_counter_ns()
            cpu_start = time.process_time_ns()
            outputs[worker] = assess_with_workers(bundle, active, worker)
            wall = (time.perf_counter_ns() - wall_start) / 1e6
            cpu = (time.process_time_ns() - cpu_start) / 1e6
            timings[str(worker)]["wall_ms"].append(round(wall, 3))
            timings[str(worker)]["cpu_ms"].append(round(cpu, 3))
            walls[worker] = wall
        if any(canonical(outputs[worker]) != canonical(outputs[1]) for worker in (2, 4)):
            all_pair_equal = False
            break
        for worker in (2, 4):
            pair_ratios[str(worker)].append(walls[1] / walls[worker])
    result.update({
        "full_json_equal_all_pairs": all_pair_equal and len(orders) == REPETITIONS,
        "orders": orders,
        "timing_ms": timings,
        "median_wall_ms": {str(worker): round(statistics.median(timings[str(worker)]["wall_ms"]), 3)
                           for worker in WORKERS},
        "median_paired_speedup_1_over_candidate": {
            str(worker): round(statistics.median(pair_ratios[str(worker)]), 4)
            for worker in (2, 4)
        } if all_pair_equal else {},
    })
    return result


def benchmark() -> dict:
    manifest = verify_frozen_inputs()
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    scenarios = {name: benchmark_scenario(name, manifest, bundle) for name in SCENARIOS}
    exact = all(all(item["initial_exact_json_equal_to_worker1"].values())
                for item in scenarios.values())
    result = {
        "status": "NO_GO_EXACT_OUTPUT" if not exact else "PENDING_SCORE",
        "scenarios": scenarios,
        "provenance": {
            "protocol_sha256": sha256(PROTOCOL),
            "script_sha256": sha256(Path(__file__)),
            "v5_code_sha256": sha256(ROOT / "scripts/assess_gpu_shape_microbatch_v5.py"),
            "model_sha256": shape.MODEL_SHA256,
            "source_sha256": shape.SOURCE_SHA256,
        },
    }
    if not exact:
        return result
    if not all(item["full_json_equal_all_pairs"] for item in scenarios.values()):
        result["status"] = "NO_GO_EXACT_OUTPUT"
        return result
    memory = {name: {str(worker): run_memory_worker(name, worker) for worker in WORKERS}
              for name in SCENARIOS}
    checks = {}
    for worker in (2, 4):
        speed = {name: scenarios[name]["median_paired_speedup_1_over_candidate"][str(worker)]
                 for name in SCENARIOS}
        gates = {
            "copied_speedup_at_least_1_10": speed["copied_slo"] >= 1.10,
            "shared_ratio_at_least_0_90": speed["shared_slo"] >= 0.90,
            "unique_ratio_at_least_0_90": speed["unique_grids"] >= 0.90,
            "peak_rss_increase_at_most_20_mib": all(
                memory[name][str(worker)]["peak_rss_mib"]
                - memory[name]["1"]["peak_rss_mib"] <= 20
                for name in SCENARIOS),
        }
        checks[str(worker)] = {
            "speed_ratios": speed,
            "gates": gates,
            "passes": all(gates.values()),
        }
    passing = [worker for worker in (2, 4) if checks[str(worker)]["passes"]]
    chosen = max(passing, key=lambda worker: (
        min(checks[str(worker)]["speed_ratios"].values()), -worker,
    )) if passing else 1
    result.update({
        "status": "GO_MULTIWORKER" if chosen != 1 else "KEEP_ONE_WORKER",
        "chosen_workers": chosen,
        "memory": memory,
        "checks": checks,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-worker", choices=SCENARIOS)
    parser.add_argument("--workers", type=int, choices=WORKERS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.memory_worker:
        if args.workers is None:
            parser.error("--workers required with --memory-worker")
        print(json.dumps(memory_worker(args.memory_worker, args.workers), sort_keys=True))
        return
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("status",)
                      if key in result} | {"chosen_workers": result.get("chosen_workers")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
