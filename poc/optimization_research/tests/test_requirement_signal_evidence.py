"""Request binding, replay, and fail-closed boundaries for research signals."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import assess_requirement_signals as assessor
import generate_requirement_signal_evidence as generator
import validate_requirement_signal_evidence as validator


class RequirementSignalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = json.loads(
            (ROOT / 'examples/requirement_signals_request.json').read_text())
        cls.saved_result = json.loads(
            (ROOT / 'examples/requirement_signals_example_result.json').read_text())
        cls.evidence = generator.generate_requirement_signal_evidence(
            cls.request, generated_at='2026-09-29T05:00:00Z')

    def test_saved_models_preserve_both_independent_signals_and_replay(self):
        result = self.evidence['result']
        self.assertEqual(result, self.saved_result)
        self.assertEqual(result['signals']['observed_cuda_reserved_peak_gib']['estimate'],
                         0.20821557769515572)
        self.assertEqual(result['signals']['cached_local_load_seconds']['estimate'],
                         0.1875111137242485)
        self.assertEqual(self.evidence['scope']['deployment_authority'], False)
        self.assertEqual(self.evidence['scope']['input_metadata_verified'], False)
        self.assertEqual(self.evidence['request_sha256'], hashlib.sha256(
            generator.canonical_bytes(self.request)).hexdigest())
        validator.validate_requirement_signal_evidence(
            self.evidence, request=self.request)
        later = generator.generate_requirement_signal_evidence(
            self.request, generated_at='2026-09-29T05:00:01Z')
        self.assertEqual(self.evidence['evidence_id'], later['evidence_id'])
        self.assertNotEqual(self.evidence['generated_at'], later['generated_at'])

    def test_declared_metadata_changes_bind_to_new_request(self):
        variants = []
        parameter = copy.deepcopy(self.request)
        parameter['parameter_metadata']['model_parameter_count'] = 21_000_000
        parameter['parameter_metadata']['model_parameter_bytes'] = 84_000_000
        variants.append(parameter)
        availability = copy.deepcopy(self.request)
        availability['parameter_metadata']['available_at'] = '2026-09-28T10:00:00Z'
        variants.append(availability)
        for changed in variants:
            with self.subTest(changed=changed['parameter_metadata']):
                other = generator.generate_requirement_signal_evidence(
                    changed, generated_at='2026-09-29T05:00:00Z')
                self.assertNotEqual(self.evidence['request_sha256'],
                                    other['request_sha256'])
                self.assertNotEqual(self.evidence['evidence_id'],
                                    other['evidence_id'])
                with self.assertRaisesRegex(ValueError, 'original_request_mismatch'):
                    validator.validate_requirement_signal_evidence(
                        self.evidence, request=changed)

    def test_one_signal_can_abstain_while_other_estimates(self):
        request = copy.deepcopy(self.request)
        request.update(batch_size=2, max_length=32, request_concurrency=1)
        evidence = generator.generate_requirement_signal_evidence(
            request, generated_at='2026-09-29T05:00:00Z')
        signals = evidence['result']['signals']
        self.assertEqual(signals['observed_cuda_reserved_peak_gib']['status'],
                         'ABSTAIN')
        self.assertIsNone(signals['observed_cuda_reserved_peak_gib']['estimate'])
        self.assertEqual(signals['observed_cuda_reserved_peak_gib']['reasons'],
                         ['unobserved_request_configuration'])
        self.assertEqual(signals['cached_local_load_seconds']['status'],
                         'RESEARCH_ESTIMATE')
        self.assertGreater(signals['cached_local_load_seconds']['estimate'], 0)
        validator.validate_requirement_signal_evidence(evidence, request=request)

    def test_training_revision_alias_remains_abstained_in_evidence(self):
        trained = assessor.load_verified_bundles()['training_metadata'][
            'google/bert_uncased_L-2_H-128_A-2']
        for revision in (trained['revision'], trained['revision'].upper()):
            with self.subTest(revision=revision):
                request = copy.deepcopy(self.request)
                request['model_id'] = 'renamed/bert-l2'
                request['model_revision'] = revision
                request['parameter_metadata']['model_parameter_count'] = trained['parameter_count']
                request['parameter_metadata']['model_parameter_bytes'] = trained['parameter_bytes']
                evidence = generator.generate_requirement_signal_evidence(
                    request, generated_at='2026-09-29T05:00:00Z')
                for signal in evidence['result']['signals'].values():
                    self.assertEqual(signal['status'], 'ABSTAIN')
                    self.assertIn('training_revision_claimed_under_new_model_id',
                                  signal['reasons'])
                validator.validate_requirement_signal_evidence(evidence, request=request)

    def test_invalid_request_is_rejected_before_artifact_load(self):
        invalid = copy.deepcopy(self.request)
        invalid['parameter_metadata']['available_at'] = '2026-09-29T06:00:00Z'
        with patch.object(assessor, 'load_verified_bundles',
                          side_effect=AssertionError('must not load')):
            with self.assertRaisesRegex(ValueError, 'invalid_registration_time_order'):
                generator.generate_requirement_signal_evidence(invalid)
        with self.assertRaisesRegex(ValueError, 'evidence_generated_before_decision'):
            generator.generate_requirement_signal_evidence(
                self.request, generated_at='2026-09-28T11:59:59Z')

    def test_schema_and_semantics_reject_action_and_signal_tampering(self):
        changed = copy.deepcopy(self.evidence)
        changed['operation'] = 'SCALE_OUT'
        with self.assertRaisesRegex(ValueError, 'evidence_schema_validation_failed'):
            validator.validate_requirement_signal_evidence(changed)
        changed = copy.deepcopy(self.evidence)
        changed['result']['signals']['observed_cuda_reserved_peak_gib']['unit'] = 'seconds'
        changed['evidence_id'] = generator.evidence_id(changed)
        with self.assertRaisesRegex(ValueError, 'unexpected_signal_descriptor'):
            validator.validate_requirement_signal_evidence(changed)
        changed = copy.deepcopy(self.evidence)
        changed['result']['signals']['cached_local_load_seconds']['estimate'] += 1
        changed['evidence_id'] = generator.evidence_id(changed)
        with self.assertRaisesRegex(ValueError, 'assessor_replay_mismatch'):
            validator.validate_requirement_signal_evidence(changed)
        changed = copy.deepcopy(self.evidence)
        changed['result']['signals']['cached_local_load_seconds']['status'] = 'ABSTAIN'
        with self.assertRaisesRegex(ValueError, 'evidence_schema_validation_failed'):
            validator.validate_requirement_signal_evidence(changed)

    def test_request_digest_and_id_are_checked_independently(self):
        changed = copy.deepcopy(self.evidence)
        changed['request']['parameter_metadata']['available_at'] = '2026-09-28T10:00:00Z'
        with self.assertRaisesRegex(ValueError, 'request_sha256_mismatch'):
            validator.validate_requirement_signal_evidence(changed)
        changed = copy.deepcopy(self.evidence)
        changed['request_sha256'] = '0' * 64
        changed['evidence_id'] = generator.evidence_id(changed)
        with self.assertRaisesRegex(ValueError, 'request_sha256_mismatch'):
            validator.validate_requirement_signal_evidence(changed)
        changed = copy.deepcopy(self.evidence)
        changed['evidence_id'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'evidence_id_mismatch'):
            validator.validate_requirement_signal_evidence(changed)

    def test_wrong_model_hash_fails_before_deserialization(self):
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / 'changed.joblib'
            corrupt.write_bytes(b'not the fixed model')
            with patch.object(assessor.joblib, 'load',
                              side_effect=AssertionError('must not deserialize')):
                with self.assertRaisesRegex(ValueError, 'vram_model_sha256_mismatch'):
                    generator.generate_requirement_signal_evidence(
                        self.request, vram_path=corrupt)

    def test_cli_writes_atomically_without_overwrite_or_failure_output(self):
        script = ROOT / 'scripts/generate_requirement_signal_evidence.py'
        check = ROOT / 'scripts/validate_requirement_signal_evidence.py'
        request_path = ROOT / 'examples/requirement_signals_request.json'
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / 'evidence.json'
            command = [sys.executable, str(script), '--request', str(request_path),
                       '--generated-at', '2026-09-29T05:00:00Z',
                       '--output', str(output)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text()), self.evidence)
            checked = subprocess.run(
                [sys.executable, str(check), '--input', str(output),
                 '--request', str(request_path)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            collision = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(collision.returncode, 2)
            self.assertIn('output_path_already_exists', collision.stderr)
            self.assertEqual(json.loads(output.read_text()), self.evidence)
            bad_model = parent / 'bad.joblib'
            bad_model.write_bytes(b'wrong model')
            absent = parent / 'absent.json'
            failed = subprocess.run(command[:-2] + ['--vram-model', str(bad_model),
                                                     '--output', str(absent)],
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertIn('vram_model_sha256_mismatch', failed.stderr)
            self.assertFalse(absent.exists())
            self.assertFalse(list(parent.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
