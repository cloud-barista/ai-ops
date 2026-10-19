import unittest
import tempfile
import tarfile
import gzip
import io
import json
from pathlib import Path
import numpy as np
from copilot_demand_transfer import features,time_bin,START,aggregate
from delay_capacity_policy import peak_targets,mature_feedback
from train_evaluate_delay_capacity import FEATURES
from verify_copilot_demand_transfer import corrected_independently,aggregate_independently


class CopilotTests(unittest.TestCase):
    def test_timestamps_and_boundary(self):
        self.assertEqual(time_bin('2026-06-05T00:05:00Z'),(1,False))
        self.assertEqual(time_bin('2026-06-05T00:04:59.9999999Z'),(0,False))
        self.assertEqual(time_bin('2026-06-05T00:05:00'),(1,True))
        self.assertEqual(time_bin('2026-06-05T09:05:00+09:00'),(1,False))
        self.assertEqual(time_bin('2026-06-05T00:04:59.9Z'),(0,False))
        self.assertEqual(time_bin('2026-06-04T23:59:59.9999999Z'),(-1,False))

    def test_variable_length_feature_frame(self):
        y=np.arange(864,dtype=float)+1000
        f=features(y)
        self.assertEqual(f.shape[0],864)
        self.assertEqual(f.loc[12,'scale'],np.mean(y[:12]))
        self.assertEqual(f.loc[0,'weekend'],0)
        self.assertEqual(f.loc[288,'weekend'],1)

    def test_future_feature_invariance(self):
        y=np.arange(864,dtype=float)+1000;a=features(y)
        y[288:]=1e12;b=features(y)
        np.testing.assert_allclose(a[list(FEATURES)].iloc[:289],b[list(FEATURES)].iloc[:289],equal_nan=True)

    def test_matured_targets_do_not_read_eval_day(self):
        y=np.arange(864,dtype=float)+1000;p=np.full(864,1000.);m=p.copy()
        a=mature_feedback(peak_targets(y,6),p,m,6,first_origin=12)
        y[288:]=1e12;b=mature_feedback(peak_targets(y,6),p,m,6,first_origin=12)
        np.testing.assert_array_equal(a[:289],b[:289])

    def test_complete_calibration_labels(self):
        for d in (1,2,6):
            origins=np.arange(12,288-d)
            self.assertEqual(origins[-1]+d+1,288)

    def test_invalid_timestamp(self):
        with self.assertRaises(ValueError):time_bin(None)

    def _archive(self,directory,rows,member='date=2026-06-05/shard-00000.jsonl.gz'):
        path=Path(directory)/'date.2026-06-05.tar.gz'
        payload=gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode())
        with tarfile.open(path,'w:gz') as out:
            info=tarfile.TarInfo(member);info.size=len(payload)
            out.addfile(info,io.BytesIO(payload))
        return path

    def _row(self,calls):
        return {'session_id':'s','date':'2026-06-05','turns':[{'turn_id':'t','llm_calls':calls}]}

    def test_nested_archive_real_boundaries_and_duplicate_identity(self):
        calls=[{'message_id':'a','timestamp':'2026-06-05T00:04:59.9999999Z','tokens':{'prompt':10}},
               {'message_id':'b','timestamp':'2026-06-05T00:05:00Z','tokens':{'prompt':20}},
               {'message_id':'a','timestamp':'2026-06-05T00:04:59.9999999Z','tokens':{'prompt':10}},
               {'message_id':'c','timestamp':'2026-06-08T00:00:00Z','tokens':{'prompt':99}}]
        with tempfile.TemporaryDirectory() as folder:
            y,c,a=aggregate([self._archive(folder,[self._row(calls)])])
        self.assertEqual(y[:2].tolist(),[20,20]);self.assertEqual(c.sum(),3)
        self.assertEqual(a['repeated_grouping_ids'],1);self.assertEqual(a['exact_repeated_call_records'],1);self.assertEqual(a['out_of_window_calls'],1)

    def test_unknown_and_zero_prompt_are_distinct(self):
        calls=[{'message_id':str(i),'timestamp':'2026-06-05T00:00:00Z','tokens':{'prompt':p}}
               for i,p in enumerate([None,True,-1,1.0,0,5])]
        with tempfile.TemporaryDirectory() as folder:
            y,c,a=aggregate([self._archive(folder,[self._row(calls)])])
        self.assertEqual(a['invalid_prompt_calls'],4);self.assertEqual(a['zero_prompt_calls'],1)
        self.assertEqual(y[0],5);self.assertEqual(c[0],2)

    def test_archive_partition_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self._archive(folder,[self._row([])],member='date=2026-06-06/shard.jsonl.gz')
            with self.assertRaises(ValueError):aggregate([path])

    def test_independent_nanosecond_aggregation(self):
        calls=[{'message_id':'a','timestamp':'2026-06-05T00:04:59.9999999Z','tokens':{'prompt':10}},
               {'message_id':'a','timestamp':'2026-06-05T09:05:00+09:00','tokens':{'prompt':20}}]
        with tempfile.TemporaryDirectory() as folder:
            path=self._archive(folder,[self._row(calls)])
            y,c,a=aggregate([path]);yy,cc,aa=aggregate_independently([path])
        np.testing.assert_array_equal(y,yy);np.testing.assert_array_equal(c,cc)

    def test_independent_feedback_matches_reference(self):
        y=1000+np.random.default_rng(42).integers(0,1000,864).astype(float)
        p=np.full(864,1200.);m=np.full(864,1000.)
        for h in (1,2,6):
            target,b=corrected_independently(y,p,m,h)
            a=mature_feedback(peak_targets(y,h),p,m,h,first_origin=12)
            np.testing.assert_allclose(a,b,rtol=1e-14,atol=1e-10)


if __name__=='__main__':unittest.main()
