"""Physical-device and measured-support boundaries of offline v0.2 evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import sklearn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import generate_gpu_shape_evidence_v0_2 as v2


class GPUShapeEvidenceV02Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = json.loads((ROOT / 'examples/gpu_shape_request_v0_2.json').read_text())
        cls.example = json.loads((ROOT / 'artifacts/gpu_shape_evidence_v0_2_0/example_evidence.json').read_text())

    def test_saved_model_example_preserves_v01_predictions_and_validates(self):
        if sklearn.__version__ != v2.TRAIN_SKLEARN_VERSION:
            self.skipTest('pinned model requires training sklearn version')
        source = json.loads((ROOT / 'examples/gpu_shape_objective_source.json').read_text())
        with patch.object(v2, 'assess_gpu_shape', wraps=v2.assess_gpu_shape) as scorer:
            actual = v2.generate_gpu_shape_evidence_v0_2(
                self.request, objective_source=source,
                generated_at=self.example['generated_at'])
        self.assertEqual(scorer.call_count, 1)
        self.assertEqual(actual, self.example)
        v2.validate_gpu_shape_evidence_v0_2(actual, request=self.request)
        old = json.loads((ROOT / 'artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json').read_text())
        self.assertEqual(actual['status'], old['status'])
        self.assertEqual(actual['selected_shape']['gpu_replicas'],
                         old['selected_shape']['gpu_replicas'])
        self.assertEqual(actual['selected_shape']['cpu_affinity_cores'],
                         old['selected_shape']['cpu_affinity_cores'])
        for now, prior in zip(actual['candidates'], old['candidates']):
            self.assertEqual({k: v for k, v in now.items() if k != 'gpu_indices'}, prior)
        self.assertEqual(actual['support_relation']['partition'], 'train')
        self.assertIs(actual['support_relation']['independent_performance_proof'], False)

    def test_invalid_or_missing_indices_abstain_before_artifact_or_model_work(self):
        for bad in (None, [1], [0, 1], [False], [0.0], ['0']):
            with self.subTest(bad=bad):
                request = copy.deepcopy(self.request)
                if bad is None:
                    del request['candidates'][0]['gpu_indices']
                else:
                    request['candidates'][0]['gpu_indices'] = bad
                with (patch.object(v2, '_verified_support_index',
                                   side_effect=AssertionError('index read')),
                      patch.object(v2, 'load_verified_bundle',
                                   side_effect=AssertionError('model load'))):
                    evidence = v2.generate_gpu_shape_evidence_v0_2(
                        request, generated_at=self.example['generated_at'])
                self.assertEqual(evidence['status'], 'ABSTAIN')
                self.assertNotIn('selected_shape', evidence)
                self.assertNotIn('candidates', evidence)
                self.assertNotIn('support_relation', evidence)
                self.assertEqual(evidence['reasons'],
                                 ['candidate_0_unsupported_or_missing_gpu_indices'])
                self.assertEqual(evidence['provenance']['support_index_check'], 'not_checked')
                v2.validate_gpu_shape_evidence_v0_2(evidence, request=request)
                altered = copy.deepcopy(evidence)
                altered['reasons'] = ['another_prevalidation_reason']
                altered['evidence_id'] = v2._evidence_id(altered)
                with self.assertRaisesRegex(ValueError, 'prevalidation_abstain_reasons_mismatch'):
                    v2.validate_gpu_shape_evidence_v0_2(altered, request=request)

    def test_unknown_top_level_constraint_abstains_before_model_and_cannot_be_forged(self):
        request = copy.deepcopy(self.request)
        request['price_usd_per_hour_max'] = 0.01
        with (patch.object(v2, '_verified_support_index',
                           side_effect=AssertionError('index read')),
              patch.object(v2, 'load_verified_bundle',
                           side_effect=AssertionError('model load'))):
            evidence = v2.generate_gpu_shape_evidence_v0_2(
                request, generated_at=self.example['generated_at'])
        self.assertEqual(evidence['status'], 'ABSTAIN')
        self.assertEqual(evidence['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertNotIn('candidates', evidence)
        self.assertNotIn('selected_shape', evidence)
        self.assertNotIn('support_relation', evidence)
        v2.validate_gpu_shape_evidence_v0_2(evidence, request=request)

        forged = copy.deepcopy(self.example)
        forged['request_sha256'] = v2.hashlib.sha256(v2._canonical_bytes(request)).hexdigest()
        forged['evidence_id'] = v2._evidence_id(forged)
        with self.assertRaisesRegex(ValueError, 'full_evidence_for_invalid_request'):
            v2.validate_gpu_shape_evidence_v0_2(forged, request=request)

    def test_support_partition_comes_from_features_and_pinned_split(self):
        index = v2._verified_support_index(v2.SOURCE_PATH, v2.SPLIT_PATH)
        counts = {}
        for partition in index.values():
            counts[partition] = counts.get(partition, 0) + 1
        self.assertEqual(counts, {
            'train': 396, 'validation': 89, 'test': 90,
            'novel_matrix': 18, 'space_filling': 44})
        for expected in counts:
            key = next(key for key, partition in index.items() if partition == expected)
            workload = dict(zip(v2.KEY_FIELDS, key))
            workload['workload_id'] = 'forged-identifier-a'
            first = v2._support_relation(workload, index)
            workload['workload_id'] = 'forged-identifier-b'
            self.assertEqual(v2._support_relation(workload, index), first)
            self.assertEqual(first['partition'], expected)
        unknown = dict(zip(v2.KEY_FIELDS, next(iter(index))))
        unknown['max_length'] = 33
        self.assertEqual(v2._support_relation(unknown, index)['partition'], 'unknown')

    def test_tampered_indices_partition_and_math_fail_even_with_new_checksum(self):
        evidence = copy.deepcopy(self.example)
        v2.validate_gpu_shape_evidence_v0_2(evidence, request=self.request)
        alterations = [
            ('candidate_index', lambda x: x['candidates'][0].update(gpu_indices=[1]),
             'candidate_gpu_indices_mismatch'),
            ('selected_index', lambda x: x['selected_shape'].update(gpu_indices=[1]),
             'selected_gpu_indices_mismatch'),
            ('partition', lambda x: x['support_relation'].update(partition='test'),
             'support_relation_mismatch'),
            ('margin', lambda x: x['candidates'][0].update(batch_latency_margin_ms=999),
             'inconsistent_candidate_objective_math'),
        ]
        for name, change, reason in alterations:
            with self.subTest(name=name):
                changed = copy.deepcopy(evidence)
                change(changed)
                changed['evidence_id'] = v2._evidence_id(changed)
                with self.assertRaisesRegex(ValueError, reason):
                    v2.validate_gpu_shape_evidence_v0_2(changed)
        forged = copy.deepcopy(evidence)
        forged['workload']['application_id'] = 'wrong-app'
        forged['evidence_id'] = v2._evidence_id(forged)
        with self.assertRaisesRegex(ValueError, 'request_workload_mismatch'):
            v2.validate_gpu_shape_evidence_v0_2(forged, request=self.request)
        for field, value in [('batch_size', 1.0), ('request_concurrency', True)]:
            changed = copy.deepcopy(evidence)
            changed['workload'][field] = value
            changed['evidence_id'] = v2._evidence_id(changed)
            with self.assertRaises(ValueError):
                v2.validate_gpu_shape_evidence_v0_2(changed)
        changed = copy.deepcopy(evidence)
        changed['selected_shape']['gpu_replicas'] = 2.0
        changed['evidence_id'] = v2._evidence_id(changed)
        with self.assertRaises(ValueError):
            v2.validate_gpu_shape_evidence_v0_2(changed)
        forged = copy.deepcopy(evidence)
        forged['workload']['workload_id'] = 'wrong-workload'
        forged['evidence_id'] = v2._evidence_id(forged)
        with self.assertRaisesRegex(ValueError, 'request_workload_mismatch'):
            v2.validate_gpu_shape_evidence_v0_2(forged, request=self.request)

    def test_modified_split_manifest_fails_before_model_load(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'split.csv'
            changed.write_bytes(v2.SPLIT_PATH.read_bytes() + b'\n')
            with patch.object(v2, 'load_verified_bundle',
                              side_effect=AssertionError('model must not load')):
                with self.assertRaisesRegex(ValueError, 'split_manifest_sha256_mismatch'):
                    v2.generate_gpu_shape_evidence_v0_2(
                        self.request, split_path=changed,
                        generated_at=self.example['generated_at'])

    def test_strict_replay_rejects_self_consistent_forged_one_gpu_choice(self):
        v2.validate_gpu_shape_evidence_v0_2_strict(
            self.example, request=self.request)
        forged = copy.deepcopy(self.example)
        row = forged['candidates'][0]
        minimum = forged['benchmark_objective']['min_requests_per_second']
        row['predicted_requests_per_second'] = minimum + 1
        row['requests_per_second_margin'] = 1.0
        row['meets_estimated_benchmark_objective'] = True
        forged['selected_shape'] = {
            'gpu_replicas': 1, 'cpu_affinity_cores': 2, 'gpu_indices': [0],
        }
        forged['evidence_id'] = v2._evidence_id(forged)
        # Structure, request binding, arithmetic, selection, and public hashes
        # all agree; only a saved-model replay can expose this forgery.
        v2.validate_gpu_shape_evidence_v0_2(forged, request=self.request)
        with self.assertRaisesRegex(ValueError, 'pinned_model_replay_mismatch'):
            v2.validate_gpu_shape_evidence_v0_2_strict(
                forged, request=self.request)

    def test_strict_replay_requires_request_and_checks_early_abstain_artifacts(self):
        with self.assertRaisesRegex(ValueError, 'strict_replay_requires_request'):
            v2.validate_gpu_shape_evidence_v0_2_strict(
                self.example, request=None)
        invalid = copy.deepcopy(self.request)
        invalid['candidates'][0]['gpu_indices'] = [1]
        abstain = v2.generate_gpu_shape_evidence_v0_2(
            invalid, generated_at=self.example['generated_at'])
        v2.validate_gpu_shape_evidence_v0_2_strict(
            abstain, request=invalid)
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / 'corrupt.joblib'
            corrupt.write_bytes(b'changed model')
            with self.assertRaisesRegex(ValueError, 'model_sha256_mismatch'):
                v2.validate_gpu_shape_evidence_v0_2_strict(
                    abstain, request=invalid, model_path=corrupt)

    def test_strict_replay_cli_rejects_forged_choice(self):
        script = ROOT / 'scripts/validate_gpu_shape_evidence_v0_2.py'
        request_path = ROOT / 'examples/gpu_shape_request_v0_2.json'
        with tempfile.TemporaryDirectory() as directory:
            genuine_path = Path(directory) / 'genuine.json'
            genuine_path.write_text(json.dumps(self.example), encoding='utf-8')
            genuine = subprocess.run(
                [sys.executable, str(script), '--input', str(genuine_path),
                 '--request', str(request_path), '--strict-replay'],
                capture_output=True, text=True)
            self.assertEqual(genuine.returncode, 0, genuine.stderr)
            forged = copy.deepcopy(self.example)
            row = forged['candidates'][0]
            minimum = forged['benchmark_objective']['min_requests_per_second']
            row['predicted_requests_per_second'] = minimum + 1
            row['requests_per_second_margin'] = 1.0
            row['meets_estimated_benchmark_objective'] = True
            forged['selected_shape'] = {
                'gpu_replicas': 1, 'cpu_affinity_cores': 2, 'gpu_indices': [0],
            }
            forged['evidence_id'] = v2._evidence_id(forged)
            input_path = Path(directory) / 'forged.json'
            input_path.write_text(json.dumps(forged), encoding='utf-8')
            base = [sys.executable, str(script), '--input', str(input_path),
                    '--request', str(request_path)]
            structural = subprocess.run(base, capture_output=True, text=True)
            self.assertEqual(structural.returncode, 0, structural.stderr)
            strict = subprocess.run(base + ['--strict-replay'],
                                    capture_output=True, text=True)
            self.assertEqual(strict.returncode, 2)
            self.assertIn('pinned_model_replay_mismatch', strict.stderr)


if __name__ == '__main__':
    unittest.main()
