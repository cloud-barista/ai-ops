"""Fixed-model call-unit lookahead policy, validation choice then new-period replay."""
import argparse,csv,datetime,hashlib,json,math
from pathlib import Path
import joblib,numpy as np,pandas as pd
from burstgpt_sparse_calls import design,targets,weighted_q90,FEATURES
from copilot_call_feedback import correct_completed
from call_capacity_control import replay,summarize,gate,COLUMNS
ROOT=Path('/home/researcher/ai-mcmp/research');PARENT=ROOT/'artifacts/burstgpt_sparse_calls_v1'
RAW=ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv';AUDIT=ROOT/'data/external/burstgpt_v2/audit.json'
START=225*288;N=90*288;TR=20*288;VE=30*288;TEST=60*288
COHORTS=('ChatGPT|API log','ChatGPT|Conversation log','GPT-4|API log','GPT-4|Conversation log')
METHODS=('stabilized','absolute','train_weighted_q90','past_hour_max','past_hour_q90','past_hour_mean')
WEIGHTS=(.25,1.,4.,16.);FACTORS=(.5,1.,2.)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2,allow_nan=False)
def output(s):return s/'artifacts/burstgpt_call_policy_v1'
def check(s):
 f=json.loads((output(s)/'FREEZE.json').read_text())
 for n,d in f['inputs'].items():assert sha(Path(n))==d,n
 for n,d in f['code'].items():assert sha(s/n)==d,n
def prepare(s):
 out=output(s);out.mkdir(parents=True,exist_ok=False)
 code=[Path(__file__),s/'scripts/call_capacity_control.py',s/'scripts/verify_burstgpt_call_policy.py',s/'tests/test_call_capacity_control.py',s/'reports/burstgpt_call_policy_protocol_v1.json']
 inputs=[RAW,AUDIT]+[PARENT/n for n in ('FREEZE.json','SELECTION.json','results.json','verification.json','counts.parquet','replay.parquet')]+list(PARENT.glob('*.joblib'))
 inputs += [ROOT/'scripts'/n for n in ('burstgpt_sparse_calls.py','copilot_call_feedback.py','verify_burstgpt_sparse_calls_precision.py')]
 assert json.loads((PARENT/'verification.json').read_text())['models_refitted']==24
 save(out/'FREEZE.json',{'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'code':{str(p.relative_to(s)):sha(p) for p in code},
  'inputs':{str(p):sha(p) for p in inputs},'protocol':json.loads(code[-1].read_text()),'frozen_before_new_period_policy_or_forecast_scores':True})
 counts=np.zeros((4,N),dtype=np.int64);total=0;previous=-math.inf
 with RAW.open(newline='') as f:
  for row in csv.DictReader(f):
   t=float(row['Timestamp']);assert np.isfinite(t) and t>=previous;previous=t
   i=int(t//300)-START
   if i>=N:break
   if i<0:continue
   counts[COHORTS.index(row['Model']+'|'+row['Log Type']),i]+=1;total+=1
 audit=json.loads(AUDIT.read_text());assert total==sum(audit['by_day'][str(d)]['parsed'] for d in range(225,315))
 bins=np.arange(START,START+N);mask=(bins*300>=audit['time']['timestamp_min_seconds'])&((bins+1)*300<=audit['time']['timestamp_max_seconds'])
 pd.DataFrame({'relative_bin':bins,'within_timestamp_extent':mask,**{n:counts[i] for i,n in enumerate(COHORTS)}}).to_parquet(out/'counts.parquet',index=False)
 save(out/'INPUT.json',{'selected_rows':total,'relative_days':[225,314],'first_full_origin':int(np.flatnonzero(mask)[0])+12,'counts_sha256':sha(out/'counts.parquet'),'raw_data_preserved':True})
 check(s);print('PREPARED',total,'records',flush=True)
def forecasts(s):
 check(s);out=output(s);counts=pd.read_parquet(out/'counts.parquet');mask=counts.within_timestamp_extent.to_numpy();first=json.loads((out/'INPUT.json').read_text())['first_full_origin']
 tables=[];parent_sel=json.loads((PARENT/'SELECTION.json').read_text())['validation_only']
 for name in COHORTS:
  y=counts[name].to_numpy(float);y[~mask]=np.nan;f=design(y);X=f[list(FEATURES)].to_numpy();m=f.scale.to_numpy();known=np.isfinite(X).all(axis=1)&np.isfinite(m)
  for h in (1,2,6):
   u=targets(y,h);train=np.arange(first,TR-h);raw={}
   for kind in ('stabilized','absolute'):
    pth=PARENT/(name.replace('|','_').replace(' ','_')+f'_h{h}_{kind}.joblib');assert sha(pth)==parent_sel[f'{name}:{h}']['model_hashes'][pth.name]
    b=joblib.load(pth);p=np.maximum(0,b['model'].predict(X));raw[kind]=p*m if kind=='stabilized' else p
   raw['train_weighted_q90']=weighted_q90(u[train]/m[train],m[train])*m
   raw['past_hour_max']=f.hour_max.to_numpy();raw['past_hour_q90']=f.hour_q90.to_numpy();raw['past_hour_mean']=f['mean'].to_numpy()
   for n,p in raw.items():
    p[~known]=np.nan;q,num,delta,last=correct_completed(u,p,m,h,first_origin=TR)
    tables.append(pd.DataFrame({'cohort':name,'horizon':h,'method':n,'origin':np.arange(N),'target':u,'raw':p,'corrected':q,'scale':m,'feedback_count':num,'delta':delta,'last_available':last}))
   print('FORECASTED',name,h,flush=True)
 pd.concat(tables,ignore_index=True).to_parquet(out/'forecasts.parquet',index=False);check(s)
def data(s):
 out=output(s);counts=pd.read_parquet(out/'counts.parquet');table=pd.read_parquet(out/'forecasts.parquet');first=json.loads((out/'INPUT.json').read_text())['first_full_origin']
 packs={}
 for name in COHORTS:
  y=counts[name].to_numpy(float);y[~counts.within_timestamp_extent.to_numpy()]=np.nan
  for h in (1,2,6):
   f=table[(table.cohort==name)&(table.horizon==h)];p={n:f[f.method==n].sort_values('origin').corrected.to_numpy() for n in METHODS}
   assert all(len(v)==N and np.isfinite(v[TR:]).all() for v in p.values())
   packs[(name,h)]=(y,p,first)
 return packs
def select(s):
 check(s);out=output(s);packs=data(s);selection={}
 for (name,h),(y,p,first) in packs.items():
  q=max(1.,float(np.quantile(y[first:TR],.9)))
  for factor in FACTORS:
   C=q/4*factor;initial=min(8,max(1,math.ceil(q/C)));valrows={}
   for n in METHODS:valrows[n]=summarize(replay(y[:VE],p[n][:VE],TR,C,h,initial),C)
   chosen=min(METHODS,key=lambda n:valrows[n]['validation_objective'])
   candidates={str(w):summarize(replay(y[:VE],p['stabilized'][:VE],TR,C,h,initial,'lookahead',w),C) for w in WEIGHTS}
   weight=min(WEIGHTS,key=lambda w:(candidates[str(w)]['validation_objective'],w))
   key=f'{name}:{h}:{factor}';selection[key]={'cohort':name,'horizon':h,'factor':factor,'capacity_calls_per_bin':C,'training_q90_calls':q,'initial_slots':initial,
    'baseline_forecast':chosen,'queue_weight':weight,'threshold_validation':valrows,'lookahead_validation':candidates}
   print('SELECTED',key,chosen,weight,flush=True)
 save(out/'SELECTION.json',{'validation_only':selection,'saved_before_new_period_test_metrics':True});check(s)
def peak_metric(u,p,ids):
 e=u[ids]-p[ids];assert np.isfinite(e).all()
 return {'rows':len(ids),'pinball':float(np.maximum(.9*e,-.1*e).mean()),'coverage':float(np.mean(u[ids]<=p[ids]))}
def evaluate(s):
 check(s);out=output(s);packs=data(s);selection=json.loads((out/'SELECTION.json').read_text())['validation_only'];results={};traces=[];forecast_metrics={}
 for key,sel in selection.items():
  name=sel['cohort'];h=sel['horizon'];y,p,_=packs[(name,h)];C=sel['capacity_calls_per_bin'];initial=sel['initial_slots']
  methods={'lookahead_primary':(p['stabilized'],'lookahead',sel['queue_weight'],VE),
   'selected_threshold':(p[sel['baseline_forecast']],'threshold',1.,None),'same_forecast_threshold':(p['stabilized'],'threshold',1.,None)}
  metrics={};carry={};daily={}
  for method,(pred,kind,w,switch) in methods.items():
   rows=replay(y,pred,TR,C,h,initial,kind,w,switch);test=rows[rows[:,0]>=TEST];metrics[method]=summarize(test,C)
   prev=rows[rows[:,0]==TEST-1][0];pre_ready=int(prev[4]);pre_active=int(prev[3])
   if pre_ready>=0 and pre_ready<=TEST:pre_active+=1;pre_ready=-1
   carry[method]={'queue_calls':float(prev[7]),'active_slots_before_first_decision':pre_active,
    'requested_slots_before_first_decision':pre_active+int(pre_ready>=0),'pending_ready_before_first_decision':pre_ready}
   for day in range(285,315):
    d=test[(test[:,0]>=(day-225)*288)&(test[:,0]<(day-224)*288)]
    daily.setdefault(str(day),{})[method]=summarize(d,C)
   f=pd.DataFrame(test,columns=COLUMNS);f['scenario']=key;f['method']=method;traces.append(f)
  status=gate(metrics['lookahead_primary'],metrics['selected_threshold']);iso=gate(metrics['lookahead_primary'],metrics['same_forecast_threshold'])
  end_state={method:{'active_slots':int(traces[-3+i].active_slots.iloc[-1]),'pending_ready':int(traces[-3+i].pending_ready.iloc[-1]),'requested_slots':int(traces[-3+i].requested_slots.iloc[-1])} for i,method in enumerate(methods)}
  results[key]={'selection':sel,'metrics':metrics,'carry_in':carry,'end_state':end_state,'daily':daily,'gate_vs_selected_threshold':status,'gate_vs_same_forecast_threshold':iso}
  print('POLICY',key,status,iso,flush=True)
 for (name,h),(y,p,_) in packs.items():
  u=targets(y,h);ids=np.arange(TEST,N-h);forecast_metrics[f'{name}:{h}']={n:peak_metric(u,v,ids) for n,v in p.items()}
 pd.concat(traces,ignore_index=True).to_parquet(out/'policy_test_replay.parquet',index=False)
 save(out/'results.json',{'scenarios':results,'forecast_test_metrics':forecast_metrics,'overall_policy_pass':all(v['gate_vs_selected_threshold']=='RETROSPECTIVE_SIMULATION_PASS' for v in results.values()),
  'new_models_trained':0,'selected_policy_scenarios':36,'abstract_calls_slots_only':True,'operational_adoption':False})
 check(s)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','forecasts','select','evaluate']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args();globals()[a.mode](a.stage)
