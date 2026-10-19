"""Unit, slate, split, and finite-sample calibration invariants."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_gpu_policy import (conformal_quantile, fit_calibration_split,
    group_calibration_scores, adaptive_group_scores, measured_feasible,
    policy_records, predicted_feasible, select, validate_slate)
from audit_data import group_ids


class GpuPolicyTests(unittest.TestCase):
    def make_slate(self, group):
        rows=[]
        for gpu in [1,2]:
            for core in [2,4,8,16]:
                rows.append({'workload_group':group,'model_id':'m','model_revision':'v',
                    'batch_size':2,'max_length':100,'request_concurrency':1,'dataset':'x',
                    'replica_count':gpu,'cpu_core_limit':core,'latency_slo_ms':100,
                    'target_throughput_rps':8,'latency_p95_ms':50,
                    'examples_per_second':10 if gpu==1 else 24,
                    'measured_requests_per_second':5 if gpu==1 else 12,
                    'predicted_latency_ms':50,'predicted_requests_per_second':5 if gpu==1 else 12,
                    'scale_latency_log':.2,'scale_throughput_log':.3,
                    'execution_id':f'{group}-{gpu}-{core}'})
        return pd.DataFrame(rows)

    def test_throughput_is_requests_per_second(self):
        slate=self.make_slate('g')
        validate_slate(slate)
        self.assertEqual(int(measured_feasible(slate).sum()),4)
        self.assertEqual(select(slate,'oracle').replica_count,2)
        self.assertEqual(select(slate,'oracle').cpu_core_limit,2)
        self.assertEqual(select(slate,'cheapest').replica_count,1)
        record=policy_records(slate,{'point':0})
        self.assertEqual(record.loc[record.policy=='cheapest','violation'].iloc[0],1)

    def test_group_max_calibration_handles_candidate_selection(self):
        slate=self.make_slate('g')
        slate.loc[slate.replica_count.eq(2)&slate.cpu_core_limit.eq(16),'latency_p95_ms']=100
        scores=group_calibration_scores(slate)
        self.assertEqual(len(scores),1)
        self.assertAlmostEqual(scores.iloc[0],np.log(2))

    def test_adaptive_boundary_uses_frozen_candidate_scales(self):
        slate=self.make_slate('g')
        slate['scale_latency_log']=.2
        slate['scale_throughput_log']=.3
        slate.loc[slate.replica_count.eq(2)&slate.cpu_core_limit.eq(16),'latency_p95_ms']=100
        self.assertAlmostEqual(adaptive_group_scores(slate).iloc[0],np.log(2)/.2)
        # Tighten only one candidate's scale; this can change which options pass.
        slate['predicted_requests_per_second']=20
        slate['target_throughput_rps']=8
        slate.loc[slate.cpu_core_limit.eq(16),'scale_latency_log']=2
        base=predicted_feasible(slate,q=.5,adaptive=False)
        adaptive=predicted_feasible(slate,q=.5,adaptive=True)
        self.assertGreater(base.sum(),adaptive.sum())

    def test_finite_sample_quantile(self):
        self.assertEqual(conformal_quantile(np.arange(1,10),.2),8)
        self.assertTrue(np.isinf(conformal_quantile(np.arange(1,10),.05)))

    def test_incomplete_slate_rejected(self):
        with self.assertRaises(ValueError):
            validate_slate(self.make_slate('g').iloc[:-1])

    def test_fit_calibration_is_disjoint_from_test_and_validation(self):
        rows=[]
        for model in ['a','b']:
            for length in range(20):
                row={'model_id':model,'model_revision':'v','batch_size':1,
                     'max_length':length+1,'request_concurrency':1,'dataset':'x'}
                rows.append(row)
        frame=pd.DataFrame(rows)
        frame['workload_group']=group_ids(frame)
        splits=fit_calibration_split(frame,42)
        self.assertTrue({'train','calibration','validation','test'} <= set(splits))
        self.assertEqual(frame.assign(split=splits).groupby('workload_group').split.nunique().max(),1)


if __name__=='__main__':
    unittest.main()
