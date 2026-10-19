"""Raw Decimal aggregation, independent controller state/value replay, all gates."""
import argparse,csv,hashlib,json,math
from collections import Counter
from decimal import Decimal
from pathlib import Path
import joblib,numpy as np,pandas as pd
from burstgpt_sparse_calls import design as exact_design,FEATURES
from verify_burstgpt_sparse_calls_precision import features as manual_features,peak,correct as independent_feedback
ROOT=Path('/home/researcher/ai-mcmp/research');PARENT=ROOT/'artifacts/burstgpt_sparse_calls_v1'
N=90*288;START=225*288;TR=20*288;VE=30*288;TEST=60*288
NAMES=('ChatGPT|API log','ChatGPT|Conversation log','GPT-4|API log','GPT-4|Conversation log')
METHODS=('stabilized','absolute','train_weighted_q90','past_hour_max','past_hour_q90','past_hour_mean');WEIGHTS=(.25,1.,4.,16.)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def utility(active,queue,forecast,t,action,C,h,weight):
 n=active;remaining=-1;q=float(queue);cost=0.;area=0.;L=h+6
 if action==1:remaining=h
 if action==-1:n-=1
 for k in range(L):
  if remaining==k:n+=1;remaining=-1
  cost+=n+int(remaining>=0)
  q=max(0.,q+forecast-n*C);area+=q/C
 return cost+weight*(area+L*q/C)
def simulate(y,p,C,h,initial,kind='threshold',weight=1.,switch_at=None):
 n=initial;ready=-1;queue=0.;last=-10**9;changed=-10**9;streak=0;rows=[]
 for t in range(TR,len(y)):
  activate=ready>=0 and ready<=t
  if activate:n+=1;ready=-1;changed=t
  prior=queue;f=float(p[t]);mode='threshold' if switch_at is not None and t<switch_at else kind
  a=0
  if not math.isfinite(f) or f<0:a=2;streak=0
  elif ready>=0:streak=0
  else:
   required=f+queue;low=n>1 and required<=.8*(n-1)*C;streak=streak+1 if low else 0
   if t-last>=2:
    down=low and streak>=3 and t-changed>=6
    if mode=='threshold':
     if required>n*C and n<8:a=1
     elif down:a=-1
    else:
     choices=[0]+([1] if n<8 else [])+([-1] if down else [])
     a=min(choices,key=lambda option:utility(n,queue,f,t,option,C,h,weight))
  if a==1:ready=t+h;last=t;streak=0
  if a==-1:n-=1;last=t;changed=t;streak=0
  cap=n*C;served=min(prior+y[t],cap);queue=max(0.,prior+y[t]-cap)
  rows.append([t,p[t],y[t],n,ready,n+int(ready>=0),prior,queue,served,max(0.,y[t]-cap),a,int(activate)])
 return np.asarray(rows,float)
def metrics(r,C):
 calls=float(sum(r[:,2]));bill=float(5*sum(r[:,5]));area=float(sum(r[:,7]));moves=[int(x) for x in r[:,10] if x in (-1,1)]
 return {'bins':len(r),'recorded_calls':calls,'billed_slot_minutes':bill,'active_slot_minutes':float(5*sum(r[:,3])),
  'instant_shortfall_fraction':float(sum(r[:,9])/calls) if calls else None,
  'queue_area_calls_bins':area,'queue_area_over_total_calls':area/calls if calls else None,'final_queue_calls':float(r[-1,7]),
  'scale_out_requests':moves.count(1),'scale_in_requests':moves.count(-1),'direction_reversals':sum(a!=b for a,b in zip(moves,moves[1:])),
  'abstentions':sum(r[:,10]==2),'validation_objective':bill/5+area/C}
def equal_dict(a,b):
 assert a.keys()==b.keys()
 for n,v in a.items():
  if v is None:assert b[n] is None
  else:np.testing.assert_allclose(v,b[n],rtol=1e-10,atol=1e-8)
def status(a,b):
 allowed=a['billed_slot_minutes']<=1.01*b['billed_slot_minutes']+1e-9 and a['instant_shortfall_fraction']<=b['instant_shortfall_fraction']+1e-12
 allowed=allowed and a['queue_area_calls_bins']<=b['queue_area_calls_bins']+1e-6 and a['final_queue_calls']<=b['final_queue_calls']+1e-6 and a['direction_reversals']<=b['direction_reversals']
 gain=a['billed_slot_minutes']<=.95*b['billed_slot_minutes'] or b['instant_shortfall_fraction']>0 and a['instant_shortfall_fraction']<=.95*b['instant_shortfall_fraction']
 return 'RETROSPECTIVE_SIMULATION_PASS' if allowed and gain else 'NO_GO'
def main(s):
 out=s/'artifacts/burstgpt_call_policy_v1';frozen=json.loads((out/'FREEZE.json').read_text())
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 for n,d in frozen['code'].items():assert sha(s/n)==d,n
 hist=Counter();total=0
 with (ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv').open(newline='') as f:
  r=csv.reader(f);next(r)
  for row in r:
   i=int(Decimal(row[0])//Decimal(300))-START
   if i>=N:break
   if i<0:continue
   hist[row[3]+'|'+row[7],i]+=1;total+=1
 counts=pd.read_parquet(out/'counts.parquet');inp=json.loads((out/'INPUT.json').read_text());assert total==inp['selected_rows'] and sha(out/'counts.parquet')==inp['counts_sha256']
 audit=json.loads((ROOT/'data/external/burstgpt_v2/audit.json').read_text());mask=np.array([i*300>=audit['time']['timestamp_min_seconds'] and (i+1)*300<=audit['time']['timestamp_max_seconds'] for i in range(START,START+N)])
 np.testing.assert_array_equal(mask,counts.within_timestamp_extent);np.testing.assert_array_equal(counts.relative_bin,np.arange(START,START+N))
 first=int(np.flatnonzero(mask)[0])+12;assert first==inp['first_full_origin']
 forecasts=pd.read_parquet(out/'forecasts.parquet');parent=pd.read_parquet(PARENT/'replay.parquet');packs={};forecastrows=0;future_checks=0
 for name in NAMES:
  y=np.array([hist[name,i] for i in range(N)],float);np.testing.assert_array_equal(y,counts[name]);y[~mask]=np.nan
  manual_X,mean,scale,maximum,q90=manual_features(y);exact=exact_design(y);X=exact[list(FEATURES)].to_numpy()
  known=np.isfinite(X).all(axis=1);np.testing.assert_array_equal(known,np.isfinite(manual_X).all(axis=1));np.testing.assert_allclose(X[known],manual_X[known],rtol=1e-13,atol=1e-13)
  mutated=y.copy();mutated[TEST:]=1e9;mutated_X=exact_design(mutated)[list(FEATURES)].to_numpy();np.testing.assert_array_equal(X[:TEST+1],mutated_X[:TEST+1])
  for h in (1,2,6):
   u=peak(y,h);tr=np.arange(first,TR-h);raw={}
   for kind in ('stabilized','absolute'):
    path=PARENT/(name.replace('|','_').replace(' ','_')+f'_h{h}_{kind}.joblib');b=joblib.load(path)
    p=np.maximum(0,b['model'].predict(X));raw[kind]=p*scale if kind=='stabilized' else p
   ratios=u[tr]/scale[tr];order=np.argsort(ratios,kind='stable');ix=np.searchsorted(np.cumsum(scale[tr][order]),.9*np.sum(scale[tr]));raw['train_weighted_q90']=ratios[order[min(ix,len(order)-1)]]*scale
   raw['past_hour_max']=maximum;raw['past_hour_q90']=q90;raw['past_hour_mean']=mean;corrected={}
   for method,p in raw.items():
    p[~known]=np.nan;q,num,delta,last=independent_feedback(u,p,scale,h);corrected[method]=q
    saved=forecasts[(forecasts.cohort==name)&(forecasts.horizon==h)&(forecasts.method==method)].sort_values('origin')
    np.testing.assert_array_equal(saved.origin,np.arange(N))
    for col,a in [('target',u),('raw',p),('corrected',q),('scale',scale),('delta',delta)]:np.testing.assert_allclose(saved[col],a,rtol=1e-10,atol=1e-10,equal_nan=True)
    np.testing.assert_array_equal(saved.feedback_count,num);np.testing.assert_array_equal(saved.last_available,last);forecastrows+=len(saved)
    previous=parent[(parent.cohort==name)&(parent.horizon==h)&(parent.method==method)].sort_values('origin')
    np.testing.assert_array_equal(saved.raw.to_numpy()[:len(previous)],previous.raw)
    np.testing.assert_array_equal(saved.corrected.to_numpy()[:len(previous)],previous.corrected)
   np.testing.assert_allclose(independent_feedback(peak(mutated,h),raw['stabilized'],scale,h)[0][:TEST+1],corrected['stabilized'][:TEST+1],rtol=0,atol=0,equal_nan=True);future_checks+=1
   packs[name,h]=(y,corrected,u)
   print('VERIFIED_FORECAST',name,h,flush=True)
 selection=json.loads((out/'SELECTION.json').read_text())['validation_only'];results=json.loads((out/'results.json').read_text());trace=pd.read_parquet(out/'policy_test_replay.parquet');scenarios=0;policyrows=0;dailychecks=0
 for key,sel in selection.items():
  name=sel['cohort'];h=sel['horizon'];y,p,u=packs[name,h];C=sel['capacity_calls_per_bin'];n=sel['initial_slots'];q=max(1.,float(np.quantile(y[first:TR],.9)))
  np.testing.assert_allclose(q,sel['training_q90_calls'],rtol=0,atol=0);assert C==q/4*sel['factor'] and n==min(8,max(1,math.ceil(q/C)))
  val={method:metrics(simulate(y[:VE],p[method][:VE],C,h,n),C) for method in METHODS}
  for method,v in val.items():equal_dict(v,sel['threshold_validation'][method])
  base=min(METHODS,key=lambda method:val[method]['validation_objective']);assert base==sel['baseline_forecast']
  candidates={w:metrics(simulate(y[:VE],p['stabilized'][:VE],C,h,n,'lookahead',w),C) for w in WEIGHTS}
  for w,v in candidates.items():equal_dict(v,sel['lookahead_validation'][str(w)])
  weight=min(WEIGHTS,key=lambda w:(candidates[w]['validation_objective'],w));assert weight==sel['queue_weight']
  rec=results['scenarios'][key];m={}
  for method,(pred,kind,w,switch) in {'lookahead_primary':(p['stabilized'],'lookahead',weight,VE),'selected_threshold':(p[base],'threshold',1.,None),'same_forecast_threshold':(p['stabilized'],'threshold',1.,None)}.items():
   allrows=simulate(y,pred,C,h,n,kind,w,switch);r=allrows[allrows[:,0]>=TEST]
   saved=trace[(trace.scenario==key)&(trace.method==method)].sort_values('origin')
   columns=['origin','forecast_calls','observed_calls','active_slots','pending_ready','requested_slots','prior_queue_calls','queue_calls','served_calls','instant_shortfall_calls','action_code','ready_activated']
   np.testing.assert_allclose(saved[columns].to_numpy(),r,rtol=1e-10,atol=1e-9,equal_nan=True);policyrows+=len(r)
   np.testing.assert_allclose(r[:,6]+r[:,2],r[:,7]+r[:,8],rtol=1e-12,atol=1e-9);assert ((r[:,3]>=1)&(r[:,3]<=8)).all()
   np.testing.assert_array_equal(r[:,5],r[:,3]+(r[:,4]>=0));np.testing.assert_allclose(r[1:,6],r[:-1,7],rtol=0,atol=0)
   m[method]=metrics(r,C);equal_dict(m[method],rec['metrics'][method])
   previous=allrows[allrows[:,0]==TEST-1][0];pre_ready=int(previous[4]);pre_active=int(previous[3])
   if pre_ready>=0 and pre_ready<=TEST:pre_active+=1;pre_ready=-1
   carry={'queue_calls':float(previous[7]),'active_slots_before_first_decision':pre_active,
    'requested_slots_before_first_decision':pre_active+int(pre_ready>=0),'pending_ready_before_first_decision':pre_ready};equal_dict(carry,rec['carry_in'][method])
   equal_dict({'active_slots':int(r[-1,3]),'pending_ready':int(r[-1,4]),'requested_slots':int(r[-1,5])},rec['end_state'][method])
   for day in range(285,315):
    d=r[(r[:,0]>=(day-225)*288)&(r[:,0]<(day-224)*288)];equal_dict(metrics(d,C),rec['daily'][str(day)][method]);dailychecks+=1
   changed=y.copy();changed[TEST:]=1e9;changedrows=simulate(changed,pred,C,h,n,kind,w,switch)
   np.testing.assert_array_equal(allrows[:TEST-TR+1,[0,1,3,4,5,6,10,11]],changedrows[:TEST-TR+1,[0,1,3,4,5,6,10,11]])
  assert status(m['lookahead_primary'],m['selected_threshold'])==rec['gate_vs_selected_threshold']
  assert status(m['lookahead_primary'],m['same_forecast_threshold'])==rec['gate_vs_same_forecast_threshold'];scenarios+=1
  print('VERIFIED_POLICY',key,flush=True)
 for (name,h),(y,p,u) in packs.items():
  ids=np.arange(TEST,N-h)
  for method,v in p.items():
   e=u[ids]-v[ids];metric={'rows':len(ids),'pinball':float(np.maximum(.9*e,-.1*e).mean()),'coverage':float(np.mean(u[ids]<=v[ids]))}
   equal_dict(metric,results['forecast_test_metrics'][f'{name}:{h}'][method])
 assert scenarios==36 and policyrows==len(trace)==933120 and forecastrows==len(forecasts)==1866240 and dailychecks==3240
 assert results['overall_policy_pass']==all(r['gate_vs_selected_threshold']=='RETROSPECTIVE_SIMULATION_PASS' for r in results['scenarios'].values())
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 with (out/'verification.json').open('x') as f:json.dump({'status':'VERIFIED','raw_records_recounted':total,'forecast_rows_checked':forecastrows,
  'old_forecast_prefix_unchanged':True,'forecast_future_mutations':future_checks,'policy_scenarios_checked':scenarios,'policy_test_rows_checked':policyrows,
  'daily_policy_metrics_checked':dailychecks,'policy_future_mutation_checks':108,'flow_conservation_and_billing_checked':True,'new_model_refits':0,'frozen_source_models_unchanged':True},f,indent=2)
 print('VERIFIED',scenarios,'scenarios',policyrows,'policy rows',flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);main(p.parse_args().stage)
