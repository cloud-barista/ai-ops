"""Check period isolation, freeze order, inference-only replay and support gates."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import polars as pl

spec = importlib.util.spec_from_file_location('v3', Path(__file__).with_name('train_alibaba_gpu_sm_hour_v3.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def workload_for_bucket(low, high):
    base = study.load_base()
    return next(f'w{i}' for i in range(10000) if low <= base.bucket(f'w{i}') < high)


def raw(start, workload, pod):
    return pl.DataFrame([{'pod_id': pod, 'workload_id': workload, 'server_id': 's',
        'gpu_request': 1., 'server_gpu_count': 1, 'gpu_spec_public': 'gpu', 'priority_class': 'HP',
        'cpu_request_cores': 4., 'avg_gpu_sm_util': float(i * 10),
        'day': (start + i) // 24, 'hour': (start + i) % 24} for i in range(4)])


class Predictor:
    def fit(self, *args, **kwargs):
        raise AssertionError('Evaluation cannot fit')

    def predict(self, frame):
        return np.full(len(frame), 4.)


class Tests(unittest.TestCase):
    def test_split_targets_stay_inside_fixed_period(self):
        for t, b, expected in [(124 * 24 + 22, 69, 'train'), (124 * 24 + 23, 69, None),
                (125 * 24, 70, 'validation'), (125 * 24 + 22, 84, 'validation'),
                (125 * 24 + 23, 84, None), (126 * 24, 85, 'test'),
                (129 * 24 + 22, 99, 'test'), (129 * 24 + 23, 99, None)]:
            self.assertEqual(study.assign_split(t, b), expected)

    def test_test_first_hour_uses_two_completed_history_windows(self):
        frame, _ = study.load_base().make_examples(raw(125 * 24 + 22, workload_for_bucket(85, 100), 'ptest'))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.t.iloc[0], 126 * 24)
        self.assertEqual(frame.sm_lag2.iloc[0], 0)
        self.assertEqual(frame.target_sm.iloc[0], 30)

    def test_train_reader_never_requests_validation_or_test_partitions(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'train.parquet'
            raw(123 * 24, workload_for_bucket(0, 70), 'ptrain').with_columns(
                pl.lit('online_inference').alias('job_type_public'),
                pl.lit('genai').alias('model_type_public')).write_parquet(path)
            def entries(_root, keys):
                self.assertEqual(min(k[0] for k in keys), 109)
                self.assertEqual(max(k[0] for k in keys), 124)
                return [str(path)], []
            with patch.object(study, 'source_entries', side_effect=entries):
                frame, _ = study.build_role('train', Path(d), study.load_base())
            self.assertEqual(len(frame), 1)

    def test_fit_freezes_selection_without_a_test_read(self):
        base = study.load_base()
        train, audit = base.make_examples(raw(123 * 24, workload_for_bucket(0, 70), 'ptrain'))
        val, _ = base.make_examples(raw(124 * 24 + 22, workload_for_bucket(70, 85), 'pval'))
        with tempfile.TemporaryDirectory() as d:
            protocol = Path(d) / 'protocol.json'
            protocol.write_text('{}')
            args = SimpleNamespace(output=Path(d) / 'output', protocol=protocol, new_root=Path(d))
            roles = []
            def build(role, *unused):
                roles.append(role)
                if role == 'test':
                    raise AssertionError('Fit tried to read held-out measurements')
                return (train if role == 'train' else val).copy(), dict(audit)
            def fit_stub(a, b, out, base):
                out.mkdir()
                (out / 'model_bundle.joblib').write_bytes(b'fixture-model')
                return {'model_sha256': study.sha(out / 'model_bundle.joblib'),
                        'selected': 'persistence', 'best_baseline': 'persistence'}
            with patch.object(study, 'build_role', side_effect=build), patch.object(study, 'train_cohort', side_effect=fit_stub):
                study.fit(args, {}, base)
            self.assertEqual(roles, ['train', 'validation'])
            freeze = json.loads((args.output / 'SELECTION_FREEZE.json').read_text())
            self.assertFalse(freeze['test_data_read'])
            self.assertEqual(len(freeze['models']), 2)

    def test_changed_frozen_model_rejected_before_test_read(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            protocol = out / 'protocol.json'
            protocol.write_text('{}')
            c = study.COHORTS[0]
            (out / c).mkdir()
            (out / c / 'model_bundle.joblib').write_bytes(b'changed')
            freeze = {'protocol_sha256': study.sha(protocol),
                      'training_script_sha256': study.sha(study.__file__),
                      'models': {c: {'sha256': 'wrong-hash'}}}
            study.write_json(out / 'SELECTION_FREEZE.json', freeze)
            args = SimpleNamespace(output=out, protocol=protocol, new_root=out)
            with patch.object(study, 'build_role') as reader:
                with self.assertRaisesRegex(ValueError, 'model changed'):
                    study.evaluate(args, {}, study.load_base())
                reader.assert_not_called()

    def test_fixed_selected_route_inference_has_no_fit(self):
        f = pd.DataFrame({'sm_current': [10., 99.], 'sm_mean3': [9., 90.], 'gpu_spec_public': ['known', 'new']})
        bundle = {'selected': 'residual_hgb_alpha_0.25', 'features': ['sm_current'],
                  'gpu_median': {'known': 5}, 'global_median': 3,
                  'models': {'direct_hgb': Predictor(), 'residual_hgb': Predictor()}}
        np.testing.assert_array_equal(study.predict_selected(f, bundle), [11., 100.])

    def test_support_and_improvement_gates(self):
        frame = pd.DataFrame([{'pod_id': f'p{i}', 'workload_id': f'w{i % 20}',
                              'day': 126 + i // 250, 'target_sm': 30., 'changed_gt10': True}
                             for i in range(1000)])
        protocol = {'support': {'rows': 1000, 'workloads': 20, 'change_events': 30, 'change_workloads': 5}}
        base = study.load_base()
        good = study.paired_result(frame, np.full(1000, 30), np.zeros(1000), protocol, base)
        self.assertEqual(good['gate'], 'PASS')
        bad = study.paired_result(frame, np.full(1000, 100), np.zeros(1000), protocol, base)
        self.assertEqual(bad['gate'], 'NO_GO')
        small = study.paired_result(frame.iloc[:10], np.full(10, 30), np.zeros(10), protocol, base)
        self.assertEqual(small['gate'], 'INSUFFICIENT_DATA')


if __name__ == '__main__':
    unittest.main()
