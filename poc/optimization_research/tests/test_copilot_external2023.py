import unittest,tempfile
from pathlib import Path
import numpy as np
from copilot_external2023 import aggregate,target,FEATURES
from train_copilot_call_demand import design

class Tests(unittest.TestCase):
 def test_recorded_timestamp_boundary_and_count_all(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.csv';p.write_text('TIMESTAMP,ContextTokens,GeneratedTokens\n2023-11-11 00:04:59.999999999,1,0\n2023-11-11 00:05:00,1,0\n2023-11-11 00:05:00,1,0\n')
   y,a=aggregate(p);self.assertEqual(list(y[:2]),[1,2]);self.assertEqual(a['rows'],3)
 def test_seven_features_do_not_depend_on_calendar(self):
  self.assertEqual(len(FEATURES),7)
  self.assertFalse(any('clock' in n or 'day' in n or 'week' in n for n in FEATURES))
  y=np.arange(400.)+1;a=design(y);y[250:]=1e9;b=design(y)
  np.testing.assert_allclose(a[list(FEATURES)].iloc[:251],b[list(FEATURES)].iloc[:251],equal_nan=True)
 def test_complete_peak_target(self):
  np.testing.assert_equal(target(np.array([1,8,3,9]),2),[8,9,np.nan,np.nan])
 def test_multiday_source_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a.csv';p.write_text('TIMESTAMP,ContextTokens,GeneratedTokens\n2023-11-11 23:59:59,1,1\n2023-11-12 00:00:00,1,1\n')
   with self.assertRaises(AssertionError):aggregate(p)
if __name__=='__main__':unittest.main()
