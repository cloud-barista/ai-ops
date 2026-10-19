import unittest,numpy as np
from copilot_call_extended import frame,baselines,N
from train_evaluate_delay_capacity import FEATURES
class Tests(unittest.TestCase):
 def test_calendar_and_lags(self):
  y=np.arange(N,dtype=float)+10;f=frame(y)
  self.assertEqual(f.loc[864,'weekend'],1);self.assertEqual(f.loc[576,'weekend'],0)
  self.assertEqual(f.loc[12,'scale'],np.mean(y[:12]));self.assertTrue(np.isfinite(f.loc[300,'previous_day_ratio']))
 def test_no_future_inputs(self):
  y=np.arange(N,dtype=float)+10;a=frame(y);y[1152:]=10**9;b=frame(y)
  np.testing.assert_allclose(a[list(FEATURES)].iloc[:1153],b[list(FEATURES)].iloc[:1153],rtol=0,atol=0,equal_nan=True)
 def test_baseline_training_labels_complete(self):
  y=np.arange(N,dtype=float)+10;f=frame(y);m=f.scale.to_numpy(float)
  for h in (1,2,6):
   a=baselines(y,m,f,h)['train_weighted_peak_q90'];y2=y.copy();y2[864:]=10**9
   b=baselines(y2,m,f,h)['train_weighted_peak_q90'];np.testing.assert_allclose(a,b,rtol=0,atol=0,equal_nan=True)
if __name__=='__main__':unittest.main()
