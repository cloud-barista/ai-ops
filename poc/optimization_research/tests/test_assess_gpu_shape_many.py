"""Call-scoped raw-prediction reuse with the original request boundary."""
import copy
import json
import sys
from pathlib import Path
import unittest

import sklearn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assess_gpu_shape import (MAX_CACHED_GRIDS, TRAIN_SKLEARN_VERSION,
                              assess_gpu_shape, assess_gpu_shape_many,
                              load_verified_bundle)
from evaluate_slo_reuse import (MULTIPLIERS, eligible_groups, make_requests,
                                verify_frozen_manifest)
from test_slo_reuse import fixture
from train_performance import ROOT


class AssessGpuShapeManyTests(unittest.TestCase):
    def test_twenty_five_slos_match_single_calls_with_one_prediction(self):
        bundle, request, model = fixture()
        requests = []
        for latency_multiplier in MULTIPLIERS:
            for rps_multiplier in MULTIPLIERS:
                changed = copy.deepcopy(request)
                changed['batch_latency_p95_limit_ms'] *= latency_multiplier
                changed['min_requests_per_second'] *= rps_multiplier
                requests.append(changed)
        expected = [assess_gpu_shape(bundle, item) for item in requests]
        self.assertEqual(model.calls, 25)
        actual = assess_gpu_shape_many(bundle, requests)
        self.assertEqual(actual, expected)
        self.assertEqual(model.calls, 26)
        self.assertIn('RESEARCH_ESTIMATE', [item['status'] for item in actual])
        self.assertIn('ABSTAIN', [item['status'] for item in actual])

    def test_invalid_unsupported_and_mixed_grids_have_exact_outputs(self):
        bundle, request, model = fixture()
        changed_revision = copy.deepcopy(request)
        for row in changed_revision['candidates']:
            row['model_revision'] = 'rev2'
        reversed_grid = copy.deepcopy(request)
        reversed_grid['candidates'].reverse()
        invalid_slo = copy.deepcopy(request)
        invalid_slo['min_requests_per_second'] = 0
        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unknown'
        requests = [request, changed_revision, invalid_slo, unsupported,
                    reversed_grid, copy.deepcopy(request),
                    copy.deepcopy(unsupported)]
        expected = [assess_gpu_shape(bundle, item) for item in requests]
        model.calls = 0
        actual = assess_gpu_shape_many(bundle, requests)
        self.assertEqual(actual, expected)
        self.assertEqual(model.calls, 3)
        self.assertEqual(actual[2]['reasons'], ['invalid_min_requests_per_second'])
        self.assertEqual(actual[3]['status'], 'ABSTAIN')
        self.assertEqual(actual[6]['status'], 'ABSTAIN')
        self.assertEqual(actual[4]['candidate_assessments'][0]['gpu_replicas'], 2)
        self.assertIsNot(actual[3]['candidate_assessments'][0]['reasons'],
                         actual[6]['candidate_assessments'][0]['reasons'])
        actual[3]['candidate_assessments'][0]['reasons'].append('changed')
        self.assertNotIn('changed',
                         actual[6]['candidate_assessments'][0]['reasons'])

    def test_invalid_request_between_identical_grids_does_not_predict(self):
        bundle, request, model = fixture()
        invalid = copy.deepcopy(request)
        invalid['batch_latency_p95_limit_ms'] = -1
        unsupported = copy.deepcopy(request)
        unsupported['price_usd_per_hour_max'] = 0.01
        missing = copy.deepcopy(request)
        del missing['min_requests_per_second']
        requests = [request, None, invalid, unsupported, missing,
                    copy.deepcopy(request)]
        actual = assess_gpu_shape_many(bundle, requests)
        self.assertEqual(model.calls, 1)
        self.assertEqual(actual[0], actual[5])
        self.assertEqual(actual[1]['reasons'], ['request_must_be_object'])
        self.assertEqual(actual[2], assess_gpu_shape(bundle, invalid))
        self.assertEqual(actual[3]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertEqual(actual[4]['reasons'],
                         ['missing_request_fields:min_requests_per_second'])
        self.assertTrue(all('selected' not in result for result in actual[1:5]))
        self.assertEqual(model.calls, 1)

    def test_unverified_and_changed_bundle_are_checked_each_call(self):
        bundle, request, model = fixture()
        self.assertEqual(len(assess_gpu_shape_many(bundle, [request])), 1)
        self.assertEqual(model.calls, 1)
        self.assertEqual(len(assess_gpu_shape_many(bundle, [request])), 1)
        self.assertEqual(model.calls, 2)  # no cache crosses the call boundary
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        expected = assess_gpu_shape(bundle, request)
        actual = assess_gpu_shape_many(bundle, [request])[0]
        self.assertEqual(actual, expected)
        self.assertEqual(actual['status'], 'ABSTAIN')
        self.assertEqual(model.calls, 4)
        bundle['_assess_gpu_shape_verified_artifacts'] = ('wrong', 'wrong')
        actual = assess_gpu_shape_many(bundle, [request, request])
        self.assertEqual(actual, [assess_gpu_shape(bundle, request)] * 2)
        self.assertEqual(model.calls, 4)

    def test_cache_is_bounded_and_input_container_is_checked(self):
        bundle, request, model = fixture()
        self.assertEqual(assess_gpu_shape_many(bundle, []), [])
        with self.assertRaisesRegex(TypeError, 'requests_must_be_list'):
            assess_gpu_shape_many(bundle, request)
        requests = []
        for index in range(MAX_CACHED_GRIDS + 1):
            changed = copy.deepcopy(request)
            for row in changed['candidates']:
                row['application_id'] = 'app-' + str(index)
            requests.append(changed)
        requests.append(copy.deepcopy(requests[0]))
        actual = assess_gpu_shape_many(bundle, requests)
        self.assertEqual(len(actual), MAX_CACHED_GRIDS + 2)
        self.assertEqual(model.calls, MAX_CACHED_GRIDS + 2)
        self.assertEqual(actual[0], actual[-1])

    @unittest.skipUnless(sklearn.__version__ == TRAIN_SKLEARN_VERSION,
                         'stored model requires its training sklearn version')
    def test_frozen_45_by_25_saved_model_full_output_parity(self):
        source_path = ROOT / 'data/audit_v1/measurements.parquet'
        model_path = ROOT / 'artifacts/performance_v1/performance_model.joblib'
        manifest = json.loads((ROOT / 'artifacts/slo_reuse_v1/manifest.json').read_text())
        verify_frozen_manifest(manifest, source_path, model_path)
        groups = eligible_groups(source_path)
        bundle = load_verified_bundle(model_path, source_path)
        self.assertEqual(bundle['model'].named_steps['regressor'].n_jobs, 2)
        compared = 0
        for record in manifest['groups']:
            requests = make_requests(groups[record['workload_group']], record)
            expected = [assess_gpu_shape(bundle, item) for item in requests]
            actual = assess_gpu_shape_many(bundle, requests)
            self.assertEqual(actual, expected, record['workload_group'])
            compared += len(requests)
        self.assertEqual(compared, 1125)
        self.assertEqual(bundle['model'].named_steps['regressor'].n_jobs, 2)


if __name__ == '__main__':
    unittest.main()
