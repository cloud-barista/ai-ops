"""One-probe information boundary and candidate-slate checks."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from conditional_response import fit_model, predict, prepare


def synthetic_slates():
    rows=[]
    for model,scale in [('prior',1.0),('new',2.0)]:
        for gpu in (1,2):
            for cores in (2,4,8,16):
                rows.append({
                    'eligible':True,'device':'cuda','model_id':model,
                    'workload_group':model,'replica_count':gpu,'cpu_core_limit':cores,
                    'batch_size':2,'max_length':128,'request_concurrency':1,
                    'repeats':8,'workload_task':'embedding',
                    'latency_slo_ms':150.0,'target_throughput_rps':4.0,
                    'latency_p95_ms':100.0*scale/gpu,
                    'examples_per_second':10.0*scale*gpu,
                    'result_json':json.dumps({'model_parameter_bytes':int(1_000_000*scale)})
                })
    return pd.DataFrame(rows)


class ConditionalResponseTests(unittest.TestCase):
    def test_unprobed_test_outcomes_do_not_change_predictions(self):
        data=synthetic_slates()
        train=prepare(data[data.model_id=='prior'])
        heldout=data[data.model_id=='new'].copy()
        model=fit_model(train,'anchor_conditioned',42)
        original=predict(model,prepare(heldout),'anchor_conditioned')
        mask=(heldout.replica_count!=1)|(heldout.cpu_core_limit!=2)
        heldout.loc[mask,'latency_p95_ms']*=10
        heldout.loc[mask,'examples_per_second']/=10
        heldout.loc[mask,'result_json']='malformed because only anchor JSON is allowed'
        changed=predict(model,prepare(heldout),'anchor_conditioned')
        np.testing.assert_allclose(original,changed)
        self.assertAlmostEqual(original[0,0],np.log(200.0))
        self.assertAlmostEqual(original[0,1],np.log(10.0))

    def test_repeated_candidate_and_inconsistent_workload_rejected(self):
        data=synthetic_slates()
        duplicate=data.copy()
        duplicate.loc[duplicate.index[-1],'cpu_core_limit']=8
        with self.assertRaises(ValueError):
            prepare(duplicate)
        inconsistent=data.copy()
        inconsistent.loc[inconsistent.index[-1],'latency_slo_ms']=151.0
        with self.assertRaises(ValueError):
            prepare(inconsistent)


if __name__=='__main__':
    unittest.main()
