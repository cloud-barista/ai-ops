"""Checks for the one-probe, leave-one-model-out transfer experiment."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from novel_transfer import (attach_anchors, choose_candidate, fit_and_predict,
                            observed_feasible, predicted_feasible,
                            relative_residual, select_remote_complete_runs)


def slates(models=('train', 'heldout')):
    records = []
    for model_index, model in enumerate(models):
        for workload_index in range(2):
            group = f'{model}-{workload_index}'
            for gpu in (1, 2):
                for cores in (2, 4, 8, 16):
                    records.append({
                        'campaign': 'khu-hf-local-exhaustive', 'model_id': model,
                        'workload_group': group, 'run_id': 'r',
                        'execution_id': f'{group}-{gpu}-{cores}',
                        'started_at': f'2026-01-01T00:00:{model_index}{workload_index}Z',
                        'batch_size': 2, 'max_length': 128 + workload_index * 64,
                        'request_concurrency': 1, 'cpu_threads': cores,
                        'cpu_core_limit': cores, 'replica_count': gpu, 'repeats': 8,
                        'workload_task': 'inference', 'latency_slo_ms': 100,
                        'target_throughput_rps': 5,
                        'latency_p95_ms': float(80 * (model_index + 1) / gpu),
                        'examples_per_second': float(6 * (model_index + 1) * gpu),
                    })
    return pd.DataFrame(records)


class NovelTransferTests(unittest.TestCase):
    def test_complete_slate_and_anchor_is_unique(self):
        frame = attach_anchors(slates())
        self.assertEqual(frame.groupby('workload_group').is_anchor.sum().to_dict(),
                         {'train-0': 1, 'train-1': 1, 'heldout-0': 1, 'heldout-1': 1})
        self.assertTrue((frame.anchor_latency_p95_ms > 0).all())
        with self.assertRaises(ValueError):
            attach_anchors(slates().iloc[:-1])

    def test_replica_prior_removes_ideal_double_throughput(self):
        frame = attach_anchors(slates(models=('one',)))
        residual = relative_residual(frame)
        self.assertTrue(np.allclose(residual[:, 1], 0))
        self.assertTrue(np.allclose(residual[frame.is_anchor.to_numpy()], 0))

    def test_unprobed_test_outcomes_cannot_affect_predictions(self):
        frame = attach_anchors(slates())
        train = frame[frame.model_id == 'train'].copy()
        heldout = frame[frame.model_id == 'heldout'].copy()
        before = fit_and_predict(train, heldout, seed=7)
        changed = heldout.copy()
        changed.loc[~changed.is_anchor, 'latency_p95_ms'] *= 10
        changed.loc[~changed.is_anchor, 'examples_per_second'] /= 10
        after = fit_and_predict(train, changed, seed=7)
        columns = [column for column in before if column.startswith('pred_')]
        pd.testing.assert_frame_equal(before[columns], after[columns])
        for prefix in ('anchor_shift', 'ratio_template', 'relative_prior'):
            anchor = before[before.is_anchor]
            self.assertTrue(np.allclose(anchor[f'pred_{prefix}_latency_p95_ms'],
                                        anchor.latency_p95_ms))
            self.assertTrue(np.allclose(anchor[f'pred_{prefix}_examples_per_second'],
                                        anchor.examples_per_second))

    def test_train_eval_model_and_group_must_be_disjoint(self):
        frame = attach_anchors(slates())
        with self.assertRaises(ValueError):
            fit_and_predict(frame, frame)
        another_group_same_model = frame[(frame.model_id == 'train') &
                                         (frame.workload_group == 'train-1')]
        train = frame[frame.workload_group == 'train-0']
        with self.assertRaises(ValueError):
            fit_and_predict(train, another_group_same_model)

    def test_slo_selection_converts_samples_to_requests(self):
        frame = attach_anchors(slates(models=('one',)))
        one = frame[frame.workload_group == 'one-0'].copy()
        one['target_throughput_rps'] = 5
        one['latency_slo_ms'] = 100
        one.loc[one.replica_count == 1, 'examples_per_second'] = 8  # 4 requests/sec
        one.loc[one.replica_count == 2, 'examples_per_second'] = 20  # 10 requests/sec
        one['pred_relative_prior_latency_p95_ms'] = one.latency_p95_ms
        one['pred_relative_prior_examples_per_second'] = one.examples_per_second
        self.assertFalse(bool(observed_feasible(one[one.is_anchor]).iloc[0]))
        self.assertEqual(int(predicted_feasible(one, 'relative_prior').sum()), 4)
        selected = choose_candidate(one, 'relative_prior')
        self.assertEqual((int(selected.replica_count), int(selected.cpu_core_limit)), (2, 2))

    def test_remote_uses_one_complete_run_per_workload(self):
        base = slates(models=('remote',)).iloc[:8].copy()
        partial = base.iloc[:7].copy()
        partial['run_id'] = 'first'
        partial['started_at'] = '2026-01-01T00:00:00Z'
        complete = base.copy()
        complete['run_id'] = 'second'
        complete['started_at'] = '2026-01-01T00:01:00Z'
        selected, runs = select_remote_complete_runs(pd.concat([partial, complete]))
        self.assertEqual(len(selected), 8)
        self.assertEqual(runs, {'remote-0': 'second'})


if __name__ == '__main__':
    unittest.main()
