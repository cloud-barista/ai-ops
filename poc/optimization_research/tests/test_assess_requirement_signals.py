"""Saved-artifact and boundary checks; no learner is fitted by these tests."""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import assess_requirement_signals as study


@unittest.skipUnless(
    study.sklearn.__version__ == study.SKLEARN_VERSION and
    study.np.__version__ == study.NUMPY_VERSION and
    study.pd.__version__ == study.PANDAS_VERSION and
    study.joblib.__version__ == study.JOBLIB_VERSION,
    'saved research artifacts require the pinned native runtime',
)
class RequirementSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles = study.load_verified_bundles()
        cls.example = json.loads((ROOT / 'examples/requirement_signals_request.json').read_text())

    def request(self) -> dict:
        return copy.deepcopy(self.example)

    def test_saved_artifacts_smoke_and_provenance(self):
        self.assertEqual(self.bundles['vram']['profile'], {
            'host': 'icns', 'device': 'cuda',
            'device_name': 'NVIDIA RTX PRO 5000 Blackwell',
            'gpu_indices_json': '[0]', 'replica_count': 1, 'cpu_core_limit': 2,
        })
        support = self.bundles['load']['task_support']['inference']
        self.assertEqual(support['model_count'], 8)
        self.assertAlmostEqual(support['min_parameter_gib'], 0.01633978635072708)
        self.assertAlmostEqual(support['max_parameter_gib'], 0.46434689313173294)
        self.assertEqual(len(self.bundles['inference_configurations']), 64)
        result = study.assess_requirement_signals(self.bundles, self.request())
        self.assertEqual(result['metadata_provenance'], 'caller_declared')
        self.assertTrue(result['artifact_hashes_verified_at_load'])
        self.assertEqual(result['source_sha256'], study.SOURCE_SHA256)
        self.assertEqual(set(result['signals']), set(study.SIGNALS))
        self.assertNotIn('vm', result['signals'])
        for signal in result['signals'].values():
            self.assertEqual(signal['status'], 'RESEARCH_ESTIMATE')
            self.assertEqual(signal['training_relation'], 'DECLARED_UNSEEN_MODEL')
            self.assertGreater(signal['estimate'], 0)
            self.assertIn(signal['artifact_sha256'], (study.VRAM_SHA256, study.LOAD_SHA256))

    def test_known_training_id_vram_abstains_load_is_labeled_in_sample(self):
        request = self.request()
        model_id = 'google/bert_uncased_L-2_H-128_A-2'
        source = self.bundles['training_metadata'][model_id]
        request['model_id'] = model_id
        request['model_revision'] = source['revision']
        request['parameter_metadata']['model_parameter_count'] = source['parameter_count']
        request['parameter_metadata']['model_parameter_bytes'] = source['parameter_bytes']
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertEqual(result['observed_cuda_reserved_peak_gib']['status'], 'ABSTAIN')
        self.assertIn('conditional_vram_requires_new_model_id',
                      result['observed_cuda_reserved_peak_gib']['reasons'])
        self.assertEqual(result['cached_local_load_seconds']['status'], 'RESEARCH_ESTIMATE')
        self.assertEqual(result['cached_local_load_seconds']['training_relation'],
                         'IN_SAMPLE_EXPLORATORY')

        request['model_revision'] = 'f' * 40
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all(signal['status'] == 'ABSTAIN' for signal in result.values()))
        self.assertTrue(all('known_model_revision_mismatch' in signal['reasons']
                            for signal in result.values()))

        request['model_revision'] = source['revision']
        request['parameter_metadata']['model_parameter_count'] += 1
        request['parameter_metadata']['model_parameter_bytes'] += 4
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('known_model_registration_metadata_mismatch'
                            in signal['reasons'] for signal in result.values()))

    def test_declared_new_id_does_not_prove_new_model(self):
        request = self.request()
        # An identifier can be changed without changing the underlying model.
        # The API reports this as a caller claim, never as independent validation.
        trained = self.bundles['training_metadata']['google/bert_uncased_L-2_H-128_A-2']
        request['parameter_metadata']['model_parameter_count'] = trained['parameter_count']
        request['parameter_metadata']['model_parameter_bytes'] = trained['parameter_bytes']
        result = study.assess_requirement_signals(self.bundles, request)
        self.assertEqual(result['metadata_provenance'], 'caller_declared')
        self.assertTrue(all(signal['training_relation'] == 'DECLARED_UNSEEN_MODEL'
                            for signal in result['signals'].values()))

    def test_training_revision_under_new_id_abstains(self):
        trained = self.bundles['training_metadata']['google/bert_uncased_L-2_H-128_A-2']
        for revision in (trained['revision'], trained['revision'].upper()):
            with self.subTest(revision=revision):
                request = self.request()
                request['model_id'] = 'renamed/bert-l2'
                request['model_revision'] = revision
                request['parameter_metadata']['model_parameter_count'] = trained['parameter_count']
                request['parameter_metadata']['model_parameter_bytes'] = trained['parameter_bytes']
                result = study.assess_requirement_signals(self.bundles, request)
                self.assertTrue(result['artifact_hashes_verified_at_load'])
                self.assertTrue(all(signal['status'] == 'ABSTAIN'
                                    for signal in result['signals'].values()))
                self.assertTrue(all('training_revision_claimed_under_new_model_id' in
                                    signal['reasons'] for signal in result['signals'].values()))

    def test_unobserved_request_configuration_only_abstains_vram(self):
        request = self.request()
        request.update(batch_size=2, max_length=32, request_concurrency=1)
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertEqual(result['observed_cuda_reserved_peak_gib']['status'], 'ABSTAIN')
        self.assertIn('unobserved_request_configuration',
                      result['observed_cuda_reserved_peak_gib']['reasons'])
        self.assertEqual(result['cached_local_load_seconds']['status'], 'RESEARCH_ESTIMATE')

    def test_outside_same_task_size_or_hardware_abstains_both(self):
        request = self.request()
        request['parameter_metadata']['model_parameter_count'] = 1_000_000_000
        request['parameter_metadata']['model_parameter_bytes'] = 4_000_000_000
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('parameter_size_outside_same_task_training_range'
                            in signal['reasons'] for signal in result.values()))
        request = self.request()
        request['hardware_profile']['cpu_core_limit'] = 4
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('unsupported_hardware_profile' in signal['reasons']
                            for signal in result.values()))

    def test_hardware_profile_requires_exact_json_types(self):
        changes = [
            ('gpu_indices', [False]),
            ('gpu_indices', [0.0]),
            ('replica_count', True),
            ('replica_count', 1.0),
            ('cpu_core_limit', 2.0),
            ('cpu_threads', 2.0),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                request = self.request()
                request['hardware_profile'][field] = value
                result = study.assess_requirement_signals(self.bundles, request)
                self.assertTrue(all(signal['status'] == 'ABSTAIN'
                                    for signal in result['signals'].values()))
                self.assertTrue(all('unsupported_hardware_profile' in signal['reasons']
                                    for signal in result['signals'].values()))

    def test_returned_profile_cannot_change_future_support_or_prediction_profile(self):
        first = study.assess_requirement_signals(self.bundles, self.request())
        first['hardware_profile']['cpu_core_limit'] = 4
        first['hardware_profile']['replica_count'] = 2
        first['hardware_profile']['gpu_indices'][0] = False
        self.assertEqual(study.EXPECTED_PROFILE['cpu_core_limit'], 2)
        self.assertEqual(study.EXPECTED_PROFILE['replica_count'], 1)
        self.assertEqual(study.EXPECTED_PROFILE['gpu_indices'], [0])

        second = study.assess_requirement_signals(self.bundles, self.request())
        self.assertEqual(second['hardware_profile'], study.EXPECTED_PROFILE)
        self.assertTrue(all(signal['status'] == 'RESEARCH_ESTIMATE'
                            for signal in second['signals'].values()))
        unsupported = self.request()
        unsupported['hardware_profile']['cpu_core_limit'] = 4
        result = study.assess_requirement_signals(self.bundles, unsupported)
        self.assertTrue(all(signal['status'] == 'ABSTAIN'
                            for signal in result['signals'].values()))

        bad = self.request()
        bad['hardware_profile']['cpu_core_limit'] = 4
        result = study.assess_requirement_signals(self.bundles, bad)
        result['signals']['observed_cuda_reserved_peak_gib']['reasons'].append('changed')
        self.assertNotIn('changed',
                         result['signals']['cached_local_load_seconds']['reasons'])

    def test_bad_registration_metadata_abstains_without_predicting(self):
        changes = [
            ('dtype', 'float16', 'unsupported_parameter_dtype'),
            ('calculation', 'numel_only', 'unsupported_parameter_calculation'),
            ('source', 'result_json', 'unsupported_registration_source'),
            ('model_parameter_bytes', 80_000_001, 'parameter_bytes_do_not_match_float32_count'),
            ('model_parameter_count', True, 'invalid_parameter_count_or_bytes'),
        ]
        for field, value, reason in changes:
            with self.subTest(field=field):
                request = self.request()
                request['parameter_metadata'][field] = value
                result = study.assess_requirement_signals(self.bundles, request)['signals']
                self.assertTrue(all(signal['status'] == 'ABSTAIN' for signal in result.values()))
                self.assertTrue(all(reason in signal['reasons'] for signal in result.values()))
        request = self.request()
        request['parameter_metadata']['available_at'] = '2026-09-29T00:00:00Z'
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('invalid_registration_time_order' in signal['reasons']
                            for signal in result.values()))
        request = self.request()
        request['workload_task'] = 'causal_lm_forward'
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('unsupported_workload_task' in signal['reasons']
                            for signal in result.values()))

    def test_unknown_fields_and_invalid_numeric_values_abstain(self):
        request = self.request()
        request['gpu_memory_reserved_bytes'] = 1000
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('request_fields_mismatch' in signal['reasons']
                            for signal in result.values()))
        request = self.request()
        request['batch_size'] = float('nan')
        result = study.assess_requirement_signals(self.bundles, request)['signals']
        self.assertTrue(all('invalid_batch_size' in signal['reasons']
                            for signal in result.values()))

    def test_unverified_in_memory_bundle_marker_abstains(self):
        bundle = dict(self.bundles)
        del bundle[study.VERIFIED_KEY]
        response = study.assess_requirement_signals(bundle, self.request())
        self.assertFalse(response['artifact_hashes_verified_at_load'])
        result = response['signals']
        self.assertTrue(all('unverified_model_bundles' in signal['reasons']
                            for signal in result.values()))

    def test_hash_and_version_fail_before_unpickling(self):
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / 'corrupt.joblib'
            corrupt.write_bytes(b'not a pinned model')
            with patch.object(study.joblib, 'load', side_effect=AssertionError('must not deserialize')):
                with self.assertRaisesRegex(ValueError, 'vram_model_sha256_mismatch'):
                    study.load_verified_bundles(vram_path=corrupt)
                with self.assertRaisesRegex(ValueError, 'load_model_sha256_mismatch'):
                    study.load_verified_bundles(load_path=corrupt)
                with self.assertRaisesRegex(ValueError, 'source_sha256_mismatch'):
                    study.load_verified_bundles(source_path=corrupt)
            with patch.object(study.sklearn, '__version__', '0.0'):
                with patch.object(study.joblib, 'load', side_effect=AssertionError('must not deserialize')):
                    with self.assertRaisesRegex(ValueError, 'sklearn_version_mismatch'):
                        study.load_verified_bundles()

    def test_verified_snapshot_survives_path_replacement_before_parse_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / 'source.parquet'
            vram_path = root / 'vram.joblib'
            load_path = root / 'load.joblib'
            source_path.write_bytes(study.SOURCE.read_bytes())
            vram_path.write_bytes(study.VRAM_MODEL.read_bytes())
            load_path.write_bytes(study.LOAD_MODEL.read_bytes())
            original_panel = study._selected_source_panel
            original_load = study.joblib.load

            def replace_source_after_verification(stream):
                self.assertIsInstance(stream, io.BytesIO)
                source_path.write_bytes(b'replaced source')
                return original_panel(stream)

            def replace_models_after_verification(stream):
                self.assertIsInstance(stream, io.BytesIO)
                vram_path.write_bytes(b'replaced vram model')
                load_path.write_bytes(b'replaced load model')
                return original_load(stream)

            with patch.object(study, '_selected_source_panel',
                              side_effect=replace_source_after_verification), \
                 patch.object(study.joblib, 'load',
                              side_effect=replace_models_after_verification):
                bundles = study.load_verified_bundles(vram_path, load_path, source_path)
            self.assertEqual(bundles[study.VERIFIED_KEY],
                             (study.SOURCE_SHA256, study.VRAM_SHA256, study.LOAD_SHA256))


if __name__ == '__main__':
    unittest.main()
