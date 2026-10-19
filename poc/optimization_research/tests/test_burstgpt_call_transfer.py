import unittest
import numpy as np
from burstgpt_call_transfer import extent_mask,targets,common_ids,supported
from train_copilot_call_demand import design
from copilot_call_feedback import correct_completed

class Tests(unittest.TestCase):
 def test_bounds_exclude_partial_bins(self):
  np.testing.assert_array_equal(extent_mask(110,1199,start_bin=0,n=5),[False,True,True,False,False])
 def test_unknown_range_not_zero(self):
  y=np.ones(40);y[0]=np.nan
  f=design(y);u=targets(y,2)
  self.assertTrue(np.isnan(f.scale.iloc[12]));self.assertEqual(f.scale.iloc[13],1)
  self.assertTrue(np.isnan(u[0]));self.assertTrue(np.isnan(u[-2:]).all())
 def test_future_target_does_not_change_current_prediction_features(self):
  y=np.arange(400.)+1;a=design(y);y[250:]=1e9;b=design(y)
  np.testing.assert_allclose(a.iloc[:251],b.iloc[:251],equal_nan=True)
 def test_maturity_at_validation_boundary(self):
  y=np.ones(600);p=y.copy();m=p.copy();a=correct_completed(targets(y,6),p,m,6,first_origin=13)[0]
  y[288:]=1000;b=correct_completed(targets(y,6),p,m,6,first_origin=13)[0]
  np.testing.assert_array_equal(a[:289],b[:289])
 def test_common_support_not_method_specific(self):
  u=np.ones(300);m=u.copy();p=u.copy();p[200]=np.nan
  ids,a=common_ids(u,m,{'a':u,'b':p},100,300)
  self.assertNotIn(200,ids);self.assertEqual(a['supported_origins'],199)
  self.assertTrue(supported(a));self.assertFalse(supported({'supported_origins':143,'supported_fraction':1}))
if __name__=='__main__':unittest.main()
