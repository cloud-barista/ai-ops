"""One-step evidence generation from the saved SQLite-trained model."""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

import sklearn


RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / 'scripts'))
from assess_gpu_shape import TRAIN_SKLEARN_VERSION
from generate_gpu_shape_evidence import generate_gpu_shape_evidence


REQUEST = RESEARCH / 'examples/gpu_shape_request.json'
OBJECTIVE_SOURCE = RESEARCH / 'examples/gpu_shape_objective_source.json'
EXAMPLE = RESEARCH / 'artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json'
SCRIPT = RESEARCH / 'scripts/generate_gpu_shape_evidence.py'


class GenerateGPUShapeEvidenceTests(unittest.TestCase):
    def test_unsupported_top_level_constraint_only_produces_abstain_evidence(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires the training scikit-learn version')
        request = json.loads(REQUEST.read_text(encoding='utf-8'))
        request['price_usd_per_hour_max'] = 0.01
        evidence = generate_gpu_shape_evidence(request,
                                               generated_at='2026-09-29T05:00:00Z')
        self.assertEqual(evidence['status'], 'ABSTAIN')
        self.assertEqual(evidence['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertNotIn('selected_shape', evidence)
        self.assertNotIn('candidates', evidence)

    def test_saved_model_generates_identical_evidence_without_intermediate_result(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires the training scikit-learn version')
        request = json.loads(REQUEST.read_text(encoding='utf-8'))
        source = json.loads(OBJECTIVE_SOURCE.read_text(encoding='utf-8'))
        expected = json.loads(EXAMPLE.read_text(encoding='utf-8'))
        actual = generate_gpu_shape_evidence(
            request, objective_source=source,
            generated_at=expected['generated_at'])
        self.assertEqual(actual, expected)
        self.assertEqual(actual['scope']['deployment_authority'], False)
        self.assertEqual(len(actual['candidates']), 8)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence.json'
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--request', str(REQUEST),
                 '--objective-source', str(OBJECTIVE_SOURCE),
                 '--generated-at', expected['generated_at'],
                 '--output', str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')),
                             expected)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_id'] = 'unmeasured-model'
        abstain = generate_gpu_shape_evidence(
            unsupported, generated_at=expected['generated_at'])
        self.assertEqual(abstain['status'], 'ABSTAIN')
        self.assertNotIn('selected_shape', abstain)
        self.assertEqual(len(abstain['candidates']), 8)
        self.assertTrue(all(row['status'] == 'ABSTAIN'
                            for row in abstain['candidates']))

    def test_changed_model_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            changed_model = directory / 'changed_model.joblib'
            changed_model.write_bytes(b'not the pinned saved model')
            output = directory / 'evidence.json'
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--request', str(REQUEST),
                 '--model', str(changed_model), '--output', str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn('model_sha256_mismatch', completed.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(list(directory.glob('*.tmp')), [])

    def test_output_collision_does_not_replace_request_or_stale_evidence(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires the training scikit-learn version')
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'request.json'
            original = REQUEST.read_bytes()
            existing.write_bytes(original)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--request', str(existing),
                 '--output', str(existing)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn('output_path_already_exists', completed.stderr)
            self.assertEqual(existing.read_bytes(), original)

            stale = Path(directory) / 'prior_evidence.json'
            stale.write_text('{"old": true}\n', encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--request', str(REQUEST),
                 '--output', str(stale)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn('output_path_already_exists', completed.stderr)
            self.assertEqual(stale.read_text(encoding='utf-8'), '{"old": true}\n')


if __name__ == '__main__':
    unittest.main()
