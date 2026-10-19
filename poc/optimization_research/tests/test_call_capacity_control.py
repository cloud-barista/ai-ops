import unittest
import numpy as np
from call_capacity_control import Config,State,decide,replay,gate

class CallPolicyTests(unittest.TestCase):
 def test_readiness_and_pending_billing(self):
  r=replay(np.zeros(6),np.full(6,100.),0,10.,2,1)
  np.testing.assert_array_equal(r[:3,3],[1,1,2]);np.testing.assert_array_equal(r[:3,5],[2,2,3])
  self.assertEqual(r[0,4],2);self.assertEqual(r[2,11],1)
 def test_flow_conservation_fractional_capacity(self):
  r=replay(np.array([1.,4.,0.,8.,2.]),np.zeros(5),0,.7,1,1)
  np.testing.assert_allclose(r[:,6]+r[:,2],r[:,7]+r[:,8],rtol=1e-12)
  np.testing.assert_allclose(r[1:,6],r[:-1,7],rtol=0,atol=0)
 def test_future_arrivals_not_in_current_action(self):
  p=np.full(20,20.);a=np.zeros(20);b=a.copy();b[7:]=1e8
  for kind in ('threshold','lookahead'):
   r=replay(a,p,0,10.,2,2,kind);z=replay(b,p,0,10.,2,2,kind)
   np.testing.assert_array_equal(r[:8,[0,1,3,4,5,6,10,11]],z[:8,[0,1,3,4,5,6,10,11]])
 def test_pending_inhibits_second_command(self):
  cfg=Config(10.,6);s=State(1,pending_ready=6)
  for kind in ('threshold','lookahead'):
   self.assertEqual(decide(s,10000.,1,cfg,kind)[0],'KEEP');self.assertEqual(s.pending_ready,6)
 def test_abstain_preserves_capacity(self):
  cfg=Config(10.,1);s=State(3,low_streak=2)
  self.assertEqual(decide(s,np.nan,9,cfg,'lookahead')[0],'ABSTAIN');self.assertEqual(s.active_slots,3);self.assertEqual(s.low_streak,0)
 def test_bounds_cooldown_and_dwell(self):
  cfg=Config(10.,1)
  s=State(8);self.assertEqual(decide(s,1000.,10,cfg,'lookahead')[0],'KEEP')
  s=State(2,last_action=9);self.assertEqual(decide(s,1000.,10,cfg,'threshold')[0],'KEEP')
  s=State(2,last_change=9,low_streak=5);self.assertEqual(decide(s,0.,10,cfg,'lookahead')[0],'KEEP')
 def test_switch_is_forward_only(self):
  y=np.arange(30)%8;p=np.full(30,20.)
  a=replay(y,p,0,5.,2,2);b=replay(y,p,0,5.,2,2,'lookahead',.25,switch_at=10)
  np.testing.assert_array_equal(a[:10],b[:10])
 def test_gate_rejects_hidden_cost_or_queue_increase(self):
  b={'billed_slot_minutes':100.,'instant_shortfall_fraction':.2,'queue_area_calls_bins':20.,'final_queue_calls':0.,'direction_reversals':4}
  a={**b,'instant_shortfall_fraction':.18};self.assertEqual(gate(a,b),'RETROSPECTIVE_SIMULATION_PASS')
  for key,v in [('billed_slot_minutes',102.),('queue_area_calls_bins',21.),('final_queue_calls',1.),('direction_reversals',5)]:self.assertEqual(gate({**a,key:v},b),'NO_GO')
 def test_invalid_readiness_not_silently_truncated(self):
  for delay in (True,1.1,0):
   with self.assertRaises(ValueError):replay(np.zeros(3),np.zeros(3),0,1.,delay,1)
  with self.assertRaises(ValueError):replay(np.array([np.nan]),np.array([1.]),0,1.,1,1)

if __name__=='__main__':unittest.main()
