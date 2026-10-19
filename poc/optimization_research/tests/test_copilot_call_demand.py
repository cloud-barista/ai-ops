import unittest
import numpy as np
from train_copilot_call_demand import design,candidates
from train_evaluate_delay_capacity import FEATURES

class CallTests(unittest.TestCase):
    def test_call_units_and_clock(self):
        y=np.arange(864,dtype=float)+10;f=design(y)
        self.assertEqual(f.loc[12,'scale'],np.mean(y[:12]))
        self.assertTrue(np.isfinite(f.loc[300,'previous_day_ratio']))
        self.assertEqual(f.loc[0,'weekend'],0);self.assertEqual(f.loc[288,'weekend'],1)
    def test_test_future_does_not_change_prior_inputs(self):
        y=np.arange(864,dtype=float)+10;a=design(y);y[576:]=10**8;b=design(y)
        np.testing.assert_allclose(a[list(FEATURES)].iloc[:577],b[list(FEATURES)].iloc[:577],rtol=0,atol=0,equal_nan=True)
    def test_train_baseline_does_not_read_later_days(self):
        y=np.arange(864,dtype=float)+10;f=design(y);m=f.scale.to_numpy(float)
        for h in (1,2,6):
            a=candidates(y,m,f,h)['train_weighted_relative_peak_q90']
            mutated=y.copy();mutated[288:]=10**8;b=candidates(mutated,m,f,h)['train_weighted_relative_peak_q90']
            np.testing.assert_allclose(a,b,rtol=0,atol=0,equal_nan=True)

if __name__=='__main__':unittest.main()
