import unittest
import numpy as np
from delay_capacity_policy import peak_targets,mature_feedback,Hold,replay_delay,choose_hold
from demand_capacity_policy import Config,replay


class DelayTests(unittest.TestCase):
    def test_peak_label_support(self):
        y=peak_targets([1,8,3,9],2)
        np.testing.assert_equal(y,[8,9,np.nan,np.nan])

    def test_target_maturity_boundary(self):
        y=np.ones(500)*1000;p=np.ones(500)*1000;m=p.copy()
        a=mature_feedback(peak_targets(y,6),p,m,6,first_origin=100,minimum=1)
        y[250]=100000
        b=mature_feedback(peak_targets(y,6),p,m,6,first_origin=100,minimum=1,window=1)
        a=mature_feedback(peak_targets(np.ones(500)*1000,6),p,m,6,first_origin=100,minimum=1,window=1)
        np.testing.assert_array_equal(a[:251],b[:251])
        self.assertGreater(b[251],a[251])

    def test_no_training_fitted_forecast_feedback(self):
        y=np.ones(1000)*100000;p=np.ones(1000)*1000;m=p.copy()
        result=mature_feedback(y,p,m,6)
        self.assertEqual(result[864+6+95],1000)
        self.assertGreater(result[864+6+96],1000)

    def test_original_hold_matches_v1(self):
        y=np.array([20,150,250,0,0,0,0,0,600,10]*5,float)
        p=np.r_[y[1:],0]
        a=replay(y,p,Config(100,startup_bins=2),2)
        b=replay_delay(y,p,100,2,2)
        for field in ('action','active_slots','pending_ready_bin','requested_slots','backlog_tokens'):
            self.assertEqual([r[field] for r in a],[r[field] for r in b])

    def test_dwell_counted_from_ready(self):
        p=[300]+[0]*12;y=[0]*13
        rows=replay_delay(y,p,100,2,1,Hold(dwell_bins=6,confirmations=1))
        self.assertEqual(rows[0]['action'],'SCALE_OUT')
        self.assertEqual(rows[2]['active_slots'],2)
        self.assertTrue(all(r['action']!='SCALE_IN' for r in rows[2:8]))
        self.assertEqual(rows[8]['action'],'SCALE_IN')

    def test_shrink_dwell_and_up_not_blocked(self):
        p=[0,0,0,300,300,300,0,0,0,0,0,0]
        rows=replay_delay(np.zeros(12),p,100,1,3,Hold(dwell_bins=6,confirmations=1))
        self.assertEqual(rows[0]['action'],'SCALE_IN')
        self.assertEqual(rows[3]['action'],'SCALE_OUT')
        self.assertTrue(all(r['action']!='SCALE_IN' for r in rows[4:10]))

    def test_future_outcome_invariance(self):
        p=np.ones(20)*100
        a=replay_delay(np.ones(20)*100,p,100,2,2,Hold(6))
        y=np.ones(20)*100;y[10:]=1e6
        b=replay_delay(y,p,100,2,2,Hold(6))
        for k in ('action','reason','active_slots','pending_ready_bin'):
            self.assertEqual([r[k] for r in a[:11]],[r[k] for r in b[:11]])

    def test_selected_hold_only_active_after_validation(self):
        p=np.array([0,0,0,0,0,500]*5,float)
        y=np.zeros(30)
        a=replay_delay(y,p,100,1,3)
        b=replay_delay(y,p,100,1,3,Hold(12,6,.6),hold_start=12)
        for k in ('action','active_slots','requested_slots','backlog_tokens'):
            self.assertEqual([r[k] for r in a[:12]],[r[k] for r in b[:12]])

    def test_selection_constraints(self):
        metrics={'billed_slot_minutes':100,'shortfall_fraction':.1,'backlog_area_over_total_tokens':.2,'final_backlog_tokens':0,'direction_reversals':5}
        reference={'hold':{'dwell_bins':0,'confirmations':3,'headroom':.8},'metrics':metrics}
        expensive={'hold':{'dwell_bins':6,'confirmations':6,'headroom':.6},'metrics':{**metrics,'billed_slot_minutes':102,'direction_reversals':0}}
        allowed={'hold':{'dwell_bins':6,'confirmations':3,'headroom':.8},'metrics':{**metrics,'billed_slot_minutes':101,'direction_reversals':2}}
        self.assertEqual(choose_hold([reference,expensive,allowed]),allowed)

    def test_invalid_arguments(self):
        for h in (-1,True,10):
            with self.assertRaises(ValueError):peak_targets(np.ones(5),h)
        with self.assertRaises(ValueError):Hold(dwell_bins=-1)
        with self.assertRaises(ValueError):Hold(headroom=1)
        with self.assertRaises(ValueError):replay_delay([0],[0],100,1,0)


if __name__=='__main__':unittest.main()
