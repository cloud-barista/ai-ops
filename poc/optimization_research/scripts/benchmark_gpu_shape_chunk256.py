"""Pinned, paired 64-vs-256 grid offline assessment benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v4 import assess_gpu_shape_microbatch_v4 as assess
from benchmark_gpu_shape_microbatch_v4 import (
    MODEL, SOURCE, SCENARIOS, canonical, ordered_requests, request_blocks,
    verify_frozen_inputs,
)
from train_performance import ROOT


PROTOCOL = ROOT / "reports/decision_inference_chunk256_protocol.md"
OUT = ROOT / "artifacts/gpu_shape_chunk256_v1/results.json"
BATCHES = (64, 256)
REPETITIONS = 7


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assess_at(bundle: dict, requests: list[dict], batch: int) -> list[dict]:
    if batch not in BATCHES:
        raise ValueError("unexpected batch size")
    # The production cap stays 64 until this experiment passes. Only the
    # existing v4 validation bound is relaxed within a single research call.
    with patch.object(shape, "MAX_CACHED_GRIDS", max(shape.MAX_CACHED_GRIDS, batch)):
        return assess(bundle, requests, max_grids=batch)


def memory_worker(scenario: str, batch: int) -> dict:
    manifest = verify_frozen_inputs()
    requests = ordered_requests(request_blocks(scenario, manifest), None)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    outputs = assess_at(bundle, requests, batch)
    return {
        "scenario": scenario, "batch": batch, "outputs": len(outputs),
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
    }


def run_memory_worker(scenario: str, batch: int) -> dict:
    proc = subprocess.run([
        sys.executable, str(Path(__file__).resolve()),
        "--memory-worker", scenario, "--batch", str(batch),
    ], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def scenario_benchmark(scenario: str, manifest: dict, bundle: dict) -> dict:
    blocks = request_blocks(scenario, manifest)
    requests = ordered_requests(blocks, None)
    initial = {}
    calls = {}
    for batch in BATCHES:
        with patch.object(shape, "_predict_once", wraps=shape._predict_once) as predict:
            initial[batch] = assess_at(bundle, requests, batch)
        calls[str(batch)] = predict.call_count
    reference = canonical(initial[64])
    if canonical(initial[256]) != reference:
        raise AssertionError(f"initial full JSON mismatch: {scenario}")
    for batch in BATCHES:
        assess_at(bundle, requests, batch)

    timings = {str(batch): {"wall_ms": [], "cpu_ms": []} for batch in BATCHES}
    ratios = []
    for repetition in range(REPETITIONS):
        active = ordered_requests(blocks, repetition)
        order = BATCHES if repetition % 2 == 0 else BATCHES[::-1]
        outputs = {}
        walls = {}
        for batch in order:
            wall_start = time.perf_counter_ns()
            cpu_start = time.process_time_ns()
            outputs[batch] = assess_at(bundle, active, batch)
            wall = (time.perf_counter_ns() - wall_start) / 1e6
            cpu = (time.process_time_ns() - cpu_start) / 1e6
            timings[str(batch)]["wall_ms"].append(round(wall, 3))
            timings[str(batch)]["cpu_ms"].append(round(cpu, 3))
            walls[batch] = wall
        if canonical(outputs[64]) != canonical(outputs[256]):
            raise AssertionError(f"paired full JSON mismatch: {scenario}/{repetition}")
        ratios.append(walls[64] / walls[256])

    return {
        "scenario": scenario, "requests": len(requests), "blocks": len(blocks),
        "prediction_calls": calls,
        "canonical_result_sha256": hashlib.sha256(reference).hexdigest(),
        "full_json_equal_all_pairs": True,
        "timing_ms": timings,
        "median_wall_ms": {str(batch): round(statistics.median(timings[str(batch)]["wall_ms"]), 3)
                           for batch in BATCHES},
        "paired_wall_speedups_64_over_256": [round(x, 4) for x in ratios],
        "median_paired_wall_speedup_64_over_256": round(statistics.median(ratios), 4),
    }


def benchmark() -> dict:
    manifest = verify_frozen_inputs()
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    scenarios = {name: scenario_benchmark(name, manifest, bundle) for name in SCENARIOS}
    memory = {name: {str(batch): run_memory_worker(name, batch) for batch in BATCHES}
              for name in SCENARIOS}
    ratios = {name: scenarios[name]["median_paired_wall_speedup_64_over_256"]
              for name in SCENARIOS}
    gates = {
        "full_json_all_scenarios": all(item["full_json_equal_all_pairs"]
                                       for item in scenarios.values()),
        "unique_prediction_calls_9_to_3": scenarios["unique_grids"]["prediction_calls"]
                                        == {"64": 9, "256": 3},
        "unique_speedup_at_least_1_10": ratios["unique_grids"] >= 1.10,
        "shared_speed_ratio_at_least_0_90": ratios["shared_slo"] >= 0.90,
        "copied_speed_ratio_at_least_0_90": ratios["copied_slo"] >= 0.90,
        "peak_rss_increase_at_most_20_mib": all(
            memory[name]["256"]["peak_rss_mib"] - memory[name]["64"]["peak_rss_mib"] <= 20
            for name in SCENARIOS),
    }
    return {
        "status": "GO_CHUNK256" if all(gates.values()) else "NO_GO_CHUNK256",
        "repetitions": REPETITIONS, "scenarios": scenarios, "memory": memory,
        "gates": gates,
        "provenance": {
            "protocol_sha256": sha256(PROTOCOL),
            "script_sha256": sha256(Path(__file__)),
            "v4_code_sha256": sha256(ROOT / "scripts/assess_gpu_shape_microbatch_v4.py"),
            "v5_code_sha256": sha256(ROOT / "scripts/assess_gpu_shape_microbatch_v5.py"),
            "model_sha256": shape.MODEL_SHA256,
            "source_sha256": shape.SOURCE_SHA256,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-worker", choices=SCENARIOS)
    parser.add_argument("--batch", type=int, choices=BATCHES)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.memory_worker:
        if args.batch is None:
            parser.error("--batch required with --memory-worker")
        print(json.dumps(memory_worker(args.memory_worker, args.batch), sort_keys=True))
        return
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "gates": result["gates"]}, sort_keys=True))


if __name__ == "__main__":
    main()
