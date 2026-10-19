"""Posthoc balanced-order timing check for max_grids=8 versus 64."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v4 import assess_gpu_shape_microbatch_v4 as assess
from benchmark_gpu_shape_microbatch_v4 import (
    MODEL, SOURCE, SCENARIOS, canonical, ordered_requests, request_blocks,
    verify_frozen_inputs,
)
from train_performance import ROOT


OUT = ROOT / "artifacts/gpu_shape_chunk64_v1/balanced_order_posthoc.json"


def main() -> None:
    manifest = verify_frozen_inputs()
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    scenarios = {}
    for scenario in SCENARIOS:
        blocks = request_blocks(scenario, manifest)
        warm = ordered_requests(blocks, None)
        assess(bundle, warm, 8)
        assess(bundle, warm, 64)
        pairs = []
        for repetition in range(4):
            active = ordered_requests(blocks, 100 + repetition)
            order = [8, 64] if repetition % 2 == 0 else [64, 8]
            outputs = {}
            walls = {}
            for batch in order:
                start = time.perf_counter_ns()
                outputs[batch] = assess(bundle, active, batch)
                walls[str(batch)] = (time.perf_counter_ns() - start) / 1e6
            if canonical(outputs[8]) != canonical(outputs[64]):
                raise AssertionError(f"balanced_output_mismatch:{scenario}:{repetition}")
            pairs.append({"order": order, "wall_ms": walls,
                          "speedup_8_over_64": walls["8"] / walls["64"]})
        scenarios[scenario] = {
            "pairs": pairs,
            "median_speedup": statistics.median(item["speedup_8_over_64"] for item in pairs),
            "min_speedup": min(item["speedup_8_over_64"] for item in pairs),
        }
    result = {
        "status": "POSTHOC_BALANCED_ORDER_CHECK",
        "scenarios": scenarios,
        "provenance": {
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "benchmark_protocol_sha256": hashlib.sha256(
                (ROOT / "reports/decision_inference_chunk64_protocol.md").read_bytes()
            ).hexdigest(),
            "model_sha256": shape.MODEL_SHA256,
            "source_sha256": shape.SOURCE_SHA256,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({name: {"median": item["median_speedup"], "min": item["min_speedup"]}
                      for name, item in scenarios.items()}, sort_keys=True))


if __name__ == "__main__":
    main()
