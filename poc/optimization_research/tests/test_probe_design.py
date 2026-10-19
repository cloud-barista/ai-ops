import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from probe_design import workload_basis,slo_boundary_weights,select_integrated_variance


class ProbeDesignTests(unittest.TestCase):
    def test_basis_and_unique_deterministic_selection(self):
        frame=pd.DataFrame({'log_batch_size':np.log([1,2,4,8,16]),
                            'log_max_length':np.log([64,128,256,512,1024]),
                            'request_concurrency':[1,2,4,8,16]})
        x=workload_basis(frame)
        self.assertEqual(x.shape,(5,7))
        first=select_integrated_variance(x,3)
        second=select_integrated_variance(x,3)
        np.testing.assert_array_equal(first,second)
        self.assertEqual(len(set(first)),3)
        self.assertTrue(all(0<=i<5 for i in first))

    def test_slo_boundary_weight_near_vs_far(self):
        latency=np.log(np.array([[100.,90.],[200.,180.]]))
        throughput=np.log(np.array([[10.,12.],[1.,2.]]))
        weights=slo_boundary_weights(latency,throughput,np.array([100.,100.]),
                                      np.array([10.,10.]))
        self.assertGreater(weights[0],weights[1])
        self.assertTrue(np.all(weights>=1.))

    def test_invalid_measurement_cost_rejected(self):
        with self.assertRaises(ValueError):
            select_integrated_variance(np.eye(3),2,predicted_cost=np.array([1.,0.,1.]))


if __name__=='__main__':
    unittest.main()
