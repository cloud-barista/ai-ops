import unittest
import numpy as np
from burstgpt_sparse_calls import design,targets,FEATURES,weighted_q90,TRAIN_END,VAL_END

class SparseCallsTests(unittest.TestCase):
 def test_zero_history_supported(self):
  f=design(np.zeros(40));self.assertTrue(np.isfinite(f[list(FEATURES)].to_numpy()[12:]).all())
  self.assertTrue((f.scale.iloc[12:]==1).all());self.assertTrue((f.hour_zero_fraction.iloc[12:]==1).all())
 def test_unknown_history_not_zero(self):
  y=np.zeros(40);y[0]=np.nan;f=design(y)
  self.assertFalse(np.isfinite(f.scale.iloc[12]));self.assertTrue(np.isfinite(f.scale.iloc[13]))
 def test_past_only_features(self):
  y=np.arange(100)%7;a=design(y);z=y.copy();z[50:]=100000;b=design(z)
  np.testing.assert_allclose(a[list(FEATURES)].to_numpy()[:51],b[list(FEATURES)].to_numpy()[:51],rtol=0,atol=0,equal_nan=True)
 def test_time_since_positive(self):
  y=np.zeros(40);y[20]=2;f=design(y)
  self.assertEqual(f.bins_since_positive.iloc[20],20);self.assertEqual(f.bins_since_positive.iloc[21],1)
  self.assertEqual(f.bins_since_positive.iloc[25],5)
 def test_target_boundaries(self):
  y=np.arange(100.);y[0]=np.nan
  for h in (1,2,6):
   u=targets(y,h);self.assertTrue(np.isnan(u[0]));self.assertTrue(np.isnan(u[-h:]).all())
   self.assertEqual(int(np.arange(13,TRAIN_END-h)[-1])+h+1,TRAIN_END)
   self.assertEqual(int(np.arange(TRAIN_END,VAL_END-h)[-1])+h+1,VAL_END)
 def test_weighted_quantile(self):
  self.assertEqual(weighted_q90(np.array([0.,2.,9.]),np.array([1.,9.,1.])),2.)

if __name__=='__main__':unittest.main()
