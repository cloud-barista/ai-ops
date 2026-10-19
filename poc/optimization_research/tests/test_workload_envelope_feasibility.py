"""Check the pre-training envelope support gate and its split boundary."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_workload_envelope_feasibility import (
    gate_summary, load_probe_manifest, oracle_config_count,
)
from evaluate_counterfactual_slo import training_threshold_grid


class WorkloadEnvelopeGateTests(unittest.TestCase):
    def test_oracle_counts_one_static_shape_across_ten_conditions(self):
        rows = []
        for group in range(10):
            for gpu in (1, 2):
                for cpu in (2, 4, 8, 16):
                    rows.append({
                        'workload_group': f'w{group}',
                        'replica_count': gpu, 'cpu_core_limit': cpu,
                        'batch_size': 2, 'latency_p95_ms': 2.0 if (gpu, cpu) == (1, 2) else 1.0,
                        'examples_per_second': 4.0,
                    })
        frame = pd.DataFrame(rows)
        count, coverage = oracle_config_count(frame, 1.5, 1.0)
        self.assertEqual(count, 7)
        self.assertEqual(coverage['gpu1_cpu2'], 0.0)
        count_arrays, _ = oracle_config_count(
            frame, np.full(len(frame), 1.5), np.full(len(frame), 1.0))
        self.assertEqual(count_arrays, 7)
        with self.assertRaises(ValueError):
            oracle_config_count(frame[frame.workload_group != 'w0'], 1.5, 1.0)

    def test_gate_requires_both_sources_and_model_spread(self):
        cases = pd.DataFrame(
            [{'model_id': f'm{i%5}', 'source': source,
              'feasible_config_count': 3 if i < (4 if source == 'original' else 30) else 8}
             for source in ('original', 'synthetic')
             for i in range(18 if source == 'original' else 450)])
        result = gate_summary(cases)
        self.assertTrue(result['overall_passes'])
        cases.loc[(cases.source == 'original') &
                  (cases.model_id == 'm3'), 'feasible_config_count'] = 8
        self.assertFalse(gate_summary(cases)['overall_passes'])

    def test_manifest_rejects_outer_leakage_and_donor_thresholds_ignore_target(self):
        models = {f'm{i}' for i in range(9)}
        entries = []
        for model in sorted(models):
            entries.append({
                'heldout_model_id': model, 'budget': 8,
                'selector': 'space_filling',
                'probe_groups_json': json.dumps([f'g{i}' for i in range(8)]),
                'training_model_ids_json': json.dumps(sorted(models - {model})),
            })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.csv'
            pd.DataFrame(entries).to_csv(path, index=False)
            self.assertEqual(len(load_probe_manifest(path, models)), 9)
            entries[0]['training_model_ids_json'] = json.dumps(sorted(models))
            pd.DataFrame(entries).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                load_probe_manifest(path, models)
        source = pd.DataFrame({
            'model_id': ['m0', 'm1', 'm2'],
            'latency_p95_ms': [999.0, 1.0, 2.0],
            'examples_per_second': [0.001, 10.0, 20.0],
            'batch_size': [1, 2, 4],
        })
        threshold = training_threshold_grid(source[source.model_id != 'm0'])
        source.loc[source.model_id == 'm0', 'latency_p95_ms'] *= 1000
        source.loc[source.model_id == 'm0', 'examples_per_second'] *= 0.001
        self.assertEqual(threshold, training_threshold_grid(
            source[source.model_id != 'm0']))


if __name__ == '__main__':
    unittest.main()
