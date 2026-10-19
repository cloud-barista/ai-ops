"""Posthoc physical lower bounds of the stated call-fluid simulation, not new gates."""
import argparse,hashlib,json,math
from pathlib import Path
import numpy as np,pandas as pd
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(s):
 out=s/'artifacts/burstgpt_call_policy_v1';counts=pd.read_parquet(out/'counts.parquet');results=json.loads((out/'results.json').read_text())
 trace=pd.read_parquet(out/'policy_test_replay.parquet');diagnosis={};checked=0
 for key,r in results['scenarios'].items():
  sel=r['selection'];C=sel['capacity_calls_per_bin'];y=counts[sel['cohort']].to_numpy(float)[60*288:90*288];total=float(y.sum())
  cap=8*C;queue=[];q=0.
  for amount in y:q=max(0.,q+amount-cap);queue.append(q)
  queue=np.asarray(queue);net=np.cumsum(y-cap);closed=net-np.minimum.accumulate(np.minimum(net,0.))
  np.testing.assert_allclose(queue,closed,rtol=1e-10,atol=1e-6)
  unavoidable=float(np.maximum(y-cap,0).sum()/total)
  checks={}
  for method,m in r['metrics'].items():
   rows=trace[(trace.scenario==key)&(trace.method==method)].sort_values('origin')
   np.testing.assert_array_equal(rows.observed_calls,y)
   assert (rows.active_slots<=8).all() and (rows.queue_calls.to_numpy()+1e-6>=queue).all()
   assert m['instant_shortfall_fraction']+1e-12>=unavoidable
   assert m['queue_area_calls_bins']+1e-6>=float(queue.sum())
   checks[method]={'excess_shortfall_percentage_points_over_bound':100*(m['instant_shortfall_fraction']-unavoidable),
    'lower_bound_share_of_reported_queue_area':float(queue.sum()/m['queue_area_calls_bins']) if m['queue_area_calls_bins'] else None}
   checked+=len(rows)
  diagnosis[key]={'capacity_calls_per_slot_bin':C,'max_slots':8,'maximum_capacity_calls_per_bin':cap,
   'test_bins_above_maximum_capacity':int((y>cap).sum()),'recorded_peak_calls':float(y.max()),
   'posthoc_slots_to_fit_single_recorded_peak':int(math.ceil(y.max()/C)),
   'unavoidable_instant_shortfall_fraction_at_always_eight_slots':unavoidable,
   'minimum_test_queue_area_with_zero_initial_queue':float(queue.sum()),'minimum_final_queue_with_zero_initial_queue':float(queue[-1]),
   'policy_comparisons':checks}
 result={'scope':'POSTHOC_DIAGNOSIS_ONLY; assumptions unchanged; no new policy/adoption selection',
  'bound':'Any valid policy has active<=8 and initial_queue>=0. Serving at8*C from test start with initial queue0 gives a favorable fluid-queue lower bound. Instant shortfall bound ignores queued demand and is not a user SLO.',
  'future_labels_used_only_for_posthoc_diagnosis':True,'real_gpu_vm_minimum_resource_label':False,
  'inputs':{p.name:sha(p) for p in (out/'counts.parquet',out/'results.json',out/'policy_test_replay.parquet')},
  'alternative_closed_form_queue_checked':True,'policy_rows_bound_checked':checked,'scenarios':diagnosis}
 assert checked==933120
 with (out/'capacity_ceiling_diagnosis.json').open('x') as f:json.dump(result,f,indent=2)
 for key,d in diagnosis.items():
  if key.endswith(':1.0'):print(key,{'minimum_shortfall':d['unavoidable_instant_shortfall_fraction_at_always_eight_slots'],
   'primary_excess_pp':d['policy_comparisons']['lookahead_primary']['excess_shortfall_percentage_points_over_bound'],
   'min_final_queue':d['minimum_final_queue_with_zero_initial_queue']})
 print('CHECKED',checked,'policy rows; gate/model/settings unchanged')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True,type=Path);main(p.parse_args().stage)
