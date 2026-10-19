"""Check source selection, model exclusion and offered-load bounds."""
import json
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_iog_gpu24_response import (
    GPU_NAME, MODEL_SLUGS, QPS_VALUES, Cell, Pair,
    fit_predict_fold, select_pairs, within_output_tolerance,
)


def synthetic_members():
    members = {}
    for model_index, (model, slug) in enumerate(MODEL_SLUGS.items()):
        for qps in QPS_VALUES:
            for count in (2, 4):
                path = (f"results/vllm_200prompts{qps}qps_{slug}_sharegpt_"
                        f"{count}x{GPU_NAME}.json")
                r2 = min(float(qps) * 1.02, 12.0 + model_index)
                t2 = 20.0 + qps + 4 * model_index
                record = {
                    "date": "20250610-120000", "backend": "vllm",
                    "model_id": model, "num_prompts": 200,
                    "gpu_name": GPU_NAME, "gpu_count": str(count),
                    "request_rate": float(qps), "completed": 200,
                    "request_throughput": (r2 if count == 2 else
                                           r2 * (1.15 + 0.08 * model_index + 0.01 * qps)),
                    "p95_ttft_ms": (t2 if count == 2 else
                                    t2 * (0.9 + 0.15 * model_index + 0.005 * qps)),
                    "total_input_tokens": 10000 + qps,
                    "total_output_tokens": 10000 + (count - 2) * 100,
                }
                members[path] = (json.dumps(record) + "\n").encode()
    return members


class IogGpu24Tests(unittest.TestCase):
    def test_complete_pairs_choose_earliest_duplicate(self):
        members = synthetic_members()
        model, slug = next(iter(MODEL_SLUGS.items()))
        path = f"results/vllm_200prompts1qps_{slug}_sharegpt_2x{GPU_NAME}.json"
        later = json.loads(members[path])
        earlier = dict(later, date="20250609-120000", request_throughput=0.75)
        members[path] += (json.dumps(earlier) + "\n").encode()
        pairs, cells = select_pairs(members)
        self.assertEqual((len(pairs), len(cells)), (15, 30))
        chosen = next(p for p in pairs if p.model_id == model and p.qps == 1)
        self.assertEqual(chosen.gpu2.request_throughput, 0.75)
        self.assertEqual(chosen.gpu2.source_line, 2)

    def test_missing_target_cell_stops_scoring(self):
        members = synthetic_members()
        members.pop(next(path for path in members if "16qps_DeepSeek-R1-Distill-Qwen-14B_" in path
                         and "_4x" in path))
        with self.assertRaisesRegex(ValueError, "Missing source cell"):
            select_pairs(members)

    def test_heldout_gpu4_outcome_cannot_change_any_prediction(self):
        pairs, _ = select_pairs(synthetic_members())
        held_model = next(iter(MODEL_SLUGS))
        train = [p for p in pairs if p.model_id != held_model]
        held = [p for p in pairs if p.model_id == held_model]
        first = fit_predict_fold(train, [p.anchor() for p in held])
        mutated = [Pair(p.gpu2, Cell(**{
            **p.gpu4.__dict__,
            "request_throughput": p.gpu4.request_throughput * 100,
            "p95_ttft_ms": p.gpu4.p95_ttft_ms * 100,
        })) for p in held]
        second = fit_predict_fold(train, [p.anchor() for p in mutated])
        for method in first:
            np.testing.assert_array_equal(first[method], second[method])
        with self.assertRaisesRegex(ValueError, "leaked"):
            fit_predict_fold(train + held[:1], [held[0].anchor()])

    def test_offered_load_cap_uses_known_qps_without_reducing_anchor(self):
        pairs, _ = select_pairs(synthetic_members())
        held_model = next(iter(MODEL_SLUGS))
        train = [p for p in pairs if p.model_id != held_model]
        held = [p for p in pairs if p.model_id == held_model]
        predictions = fit_predict_fold(train, [p.anchor() for p in held])
        for pair, estimate in zip(held, predictions["capped_offered_load"]):
            r2 = pair.gpu2.request_throughput
            expected_r4 = min(2 * r2, max(pair.qps, r2))
            self.assertAlmostEqual(r2 * math.exp(estimate[0]), expected_r4)
            self.assertEqual(estimate[1], 0.0)

    def test_output_mismatch_is_evaluation_subset_only(self):
        pairs, _ = select_pairs(synthetic_members())
        pair = pairs[0]
        self.assertTrue(within_output_tolerance(pair))
        changed = Pair(pair.gpu2, Cell(**{
            **pair.gpu4.__dict__, "total_output_tokens": pair.gpu2.total_output_tokens * 2,
        }))
        self.assertFalse(within_output_tolerance(changed))


if __name__ == "__main__":
    unittest.main()
