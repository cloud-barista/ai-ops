"""Checks for invalid labels and train/test leakage in the research pipeline."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_data import eligible_mask, group_ids
from train_performance import assign_splits, FEATURES, support_mask, SUPPORT_COMBINATION
from predict_performance import _supported_integer, predict_records


class DataContractTests(unittest.TestCase):
    def test_failed_incomplete_or_nonfinite_labels_excluded(self):
        frame = pd.DataFrame({'success':[1,0,1,1,1],
            'run_status':['complete','complete','running','complete','complete'],
            'latency_p95_ms':[2,2,2,np.nan,0], 'examples_per_second':[3,3,3,3,3]})
        self.assertEqual(eligible_mask(frame).tolist(), [True,False,False,False,False])

    def test_resource_alternatives_and_repeats_cannot_cross_split(self):
        rows = []
        for length in range(20):
            for device in ['cpu','cuda']:
                for repeat in [6,8]:
                    rows.append(dict(model_id='model',model_revision='v1',batch_size=1,
                        max_length=length+1,request_concurrency=1,dataset='text',device=device,repeats=repeat))
        frame = pd.DataFrame(rows)
        frame['workload_group'] = group_ids(frame)
        frame['split'] = assign_splits(frame)
        self.assertEqual(frame.workload_group.nunique(),20)
        self.assertTrue((frame.groupby('workload_group').split.nunique()==1).all())
        self.assertEqual(set(frame.split), {'train','validation','test'})
        shuffled = frame.sample(frac=1,random_state=9)
        pd.testing.assert_series_equal(assign_splits(frame).sort_index(),assign_splits(shuffled).sort_index())

    def test_outcomes_are_not_features(self):
        forbidden = {'latency_p95_ms','examples_per_second','max_rss_kib','reward',
                     'cpu_utilization_percent','compute_seconds','node_summary_json','success'}
        self.assertFalse(set(FEATURES) & forbidden)

    def test_unseen_model_device_pair_is_not_supported(self):
        rows = []
        for model, device in [('a','cpu'),('b','cuda')]:
            row = {key:1 for key in FEATURES}
            row.update(model_id=model,model_revision='v1',device=device,host='local',device_name=device)
            rows.append(row)
        training = pd.DataFrame(rows)
        candidate = training.iloc[[0]].copy()
        candidate['model_id'] = 'b'
        self.assertFalse(support_mask(training,candidate).iloc[0])

    def test_prediction_abstains_before_model_call_on_missing_input(self):
        bundle = {'categorical_support':{'model_id':['a']},'numeric_support':{'batch_size':[1,8]},
                  'support_combination_columns':['model_id'],'support_combinations':[('a',)]}
        for value in (float('nan'), float('inf'), True, 10**1000):
            with self.subTest(value_type=type(value).__name__):
                result=predict_records(bundle,[{'model_id':'a','batch_size':value}])
                self.assertEqual(result[0]['status'],'ABSTAIN')
                self.assertIn('missing_or_out_of_range:batch_size', result[0]['reasons'])
        self.assertTrue(_supported_integer(np.float64(2.0), 1, 8))

    def test_prediction_rejects_non_array_and_abstains_on_non_object_row(self):
        bundle = {'categorical_support':{'model_id':['a']},
                  'numeric_support':{'batch_size':[1,8]},
                  'support_combination_columns':['model_id'],
                  'support_combinations':[('a',)]}
        with self.assertRaisesRegex(ValueError, 'records_must_be_array'):
            predict_records(bundle, {'model_id':'a'})
        result = predict_records(bundle, [None, 'a', 123])
        self.assertEqual(result, [
            {'status':'ABSTAIN', 'reasons':['record_must_be_object']}] * 3)


if __name__ == '__main__':
    unittest.main()
