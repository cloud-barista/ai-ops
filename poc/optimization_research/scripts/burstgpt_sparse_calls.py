"""Separate recorded-arrival learning task with explicit sparse-history support."""
import argparse, csv, datetime, hashlib, json
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from copilot_call_feedback import correct_completed
ROOT=Path('/home/researcher/ai-mcmp/research')
RAW=ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv'
AUDIT=ROOT/'data/external/burstgpt_v2/audit.json'
START=225*288;N=60*288;TRAIN_END=20*288;VAL_END=30*288
COHORTS=('ChatGPT|API log','ChatGPT|Conversation log','GPT-4|API log','GPT-4|Conversation log')
PARAMS=dict(loss='quantile',quantile=.9,max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=20,l2_regularization=1,early_stopping=False,random_state=42)
FEATURES=('lag1_ratio','lag2_ratio','lag3_ratio','lag12_ratio','hour_max_ratio','hour_q90_ratio','recent_vs_older','log_hour_mean','hour_zero_fraction','bins_since_positive')

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False)

def design(y):
 a=pd.Series(np.asarray(y,float));past=a.shift(1);mean=past.rolling(12,min_periods=12).mean();scale=mean.clip(lower=1)
 maximum=past.rolling(12,min_periods=12).max();q=past.rolling(12,min_periods=12).quantile(.9)
 old=a.shift(7).rolling(6,min_periods=6).mean();recent=past.rolling(6,min_periods=6).mean()
 gap=np.full(len(a),np.nan);last=None;run_start=None
 for i in range(len(a)):
  if i and np.isfinite(a.iloc[i-1]):
   if run_start is None:run_start=i-1
   if a.iloc[i-1]>0:last=i-1
   gap[i]=min(288,i-last if last is not None else i-run_start)
  else:last=None;run_start=None
 f=pd.DataFrame({'mean':mean,'scale':scale,'hour_max':maximum,'hour_q90':q,
  'recent_vs_older':recent/old.clip(lower=1),'log_hour_mean':np.log1p(mean),
  'hour_zero_fraction':past.eq(0).astype(float).where(past.notna()).rolling(12,min_periods=12).mean(),
  'bins_since_positive':gap})
 for lag in (1,2,3,12):f[f'lag{lag}_ratio']=a.shift(lag)/scale
 f['hour_max_ratio']=maximum/scale;f['hour_q90_ratio']=q/scale
 return f.replace([np.inf,-np.inf],np.nan)
def targets(y,h):return np.r_[np.max(np.lib.stride_tricks.sliding_window_view(y,h+1),axis=1),np.full(h,np.nan)]
def metric(y,p,ids):
 if not len(ids):return None
 e=y[ids]-p[ids];assert np.isfinite(e).all()
 return {'rows':len(ids),'pinball':float(np.maximum(.9*e,-.1*e).mean()),'coverage':float(np.mean(y[ids]<=p[ids])),
  'mean_prediction':float(p[ids].mean()),'mean_target':float(y[ids].mean())}
def weighted_q90(y,w):
 order=np.argsort(y,kind='stable');ix=min(np.searchsorted(np.cumsum(w[order]),.9*w.sum()),len(y)-1)
 return float(y[order[ix]])
def stage_out(s):return s/'artifacts/burstgpt_sparse_calls_v1'
def check(s):
 f=json.loads((stage_out(s)/'FREEZE.json').read_text())
 for n,h in f['code'].items():assert sha(s/n)==h,n
 for n,h in f['inputs'].items():assert sha(Path(n))==h,n
def prepare(s):
 out=stage_out(s);out.mkdir(parents=True,exist_ok=False)
 protocol=s/'reports/burstgpt_sparse_calls_protocol_v1.json'
 code=[Path(__file__),s/'scripts/verify_burstgpt_sparse_calls.py',s/'tests/test_burstgpt_sparse_calls.py',protocol]
 inputs=[RAW,AUDIT,ROOT/'scripts/copilot_call_feedback.py']
 save(out/'FREEZE.json',{'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'code':{str(p.relative_to(s)):sha(p) for p in code},'inputs':{str(p):sha(p) for p in inputs},
  'params':PARAMS,'protocol':json.loads(protocol.read_text()),'frozen_before_sparse_model_fit_and_forecast_scores':True,
  'source_previous_other_target_exposure':True})
 counts=np.zeros((4,N),dtype=np.int64);total=0;previous=-1.
 with RAW.open(newline='') as f:
  for row in csv.DictReader(f):
   t=float(row['Timestamp']);assert np.isfinite(t) and t>=previous;previous=t
   i=int(t//300)-START
   if i>=N:break
   if i<0:continue
   counts[COHORTS.index(row['Model']+'|'+row['Log Type']),i]+=1;total+=1
 audit=json.loads(AUDIT.read_text());assert total==sum(audit['by_day'][str(d)]['parsed'] for d in range(225,285))
 bins=np.arange(START,START+N);mask=(bins*300>=audit['time']['timestamp_min_seconds'])&((bins+1)*300<=audit['time']['timestamp_max_seconds'])
 pd.DataFrame({'relative_bin':bins,'within_timestamp_extent':mask,**{n:counts[i] for i,n in enumerate(COHORTS)}}).to_parquet(out/'counts.parquet',index=False)
 save(out/'INPUT.json',{'selected_rows':total,'relative_days':[225,284],'counts_sha256':sha(out/'counts.parquet'),
  'first_full_origin':int(np.flatnonzero(mask)[0])+12,'inside_extent_zero_is_recorded_empty_not_proven_actual_zero':True})
 check(s);print('PREPARED',total,'records')
def train(s):
 check(s);out=stage_out(s);frame=pd.read_parquet(out/'counts.parquet');mask=frame.within_timestamp_extent.to_numpy()
 first=json.loads((out/'INPUT.json').read_text())['first_full_origin'];selection={};tables=[]
 for name in COHORTS:
  y=frame[name].to_numpy(float);y[~mask]=np.nan;f=design(y);X=f[list(FEATURES)].to_numpy();m=f.scale.to_numpy()
  for h in (1,2,6):
   u=targets(y,h);train=np.arange(first,TRAIN_END-h);val=np.arange(TRAIN_END,VAL_END-h)
   assert np.isfinite(X[train]).all() and np.isfinite(u[train]).all() and train[-1]+h+1==TRAIN_END
   raw={};hashes={}
   for kind in ('stabilized','absolute'):
    model=HistGradientBoostingRegressor(**PARAMS)
    z=u[train]/m[train] if kind=='stabilized' else u[train]
    weight=m[train]/m[train].mean() if kind=='stabilized' else np.ones(len(train))
    model.fit(X[train],z,sample_weight=weight)
    p=np.maximum(0,model.predict(X));p=p*m if kind=='stabilized' else p
    raw[kind]=p
    path=out/(name.replace('|','_').replace(' ','_')+f'_h{h}_{kind}.joblib')
    joblib.dump({'model':model,'features':FEATURES,'kind':kind,'cohort':name,'horizon':h,'train_origins':train,'params':PARAMS},path,compress=3)
    hashes[path.name]=sha(path)
   raw['train_weighted_q90']=weighted_q90(u[train]/m[train],m[train])*m
   raw['past_hour_max']=f.hour_max.to_numpy();raw['past_hour_q90']=f.hour_q90.to_numpy();raw['past_hour_mean']=f['mean'].to_numpy()
   # All methods share complete history. The stabilized scale does not replace unknown bins.
   known=np.isfinite(X).all(axis=1)&np.isfinite(m)
   corrected={}
   for n,p in raw.items():
    p[~known]=np.nan;q,count,delta,last=correct_completed(u,p,m,h,first_origin=TRAIN_END)
    corrected[n]=q
    tables.append(pd.DataFrame({'cohort':name,'horizon':h,'method':n,'origin':np.arange(N),
     'target':u,'raw':p,'corrected':q,'mean':f['mean'],'scale':m,'feedback_count':count,'delta':delta,'last_available':last}))
   candidates=[n for n in raw if n!='stabilized']
   selected=min(candidates,key=lambda n:metric(u,corrected[n],val)['pinball'])
   selection[f'{name}:{h}']={'baseline':selected,'validation':{n:metric(u,p,val) for n,p in corrected.items()},
    'model_hashes':hashes,'train_rows':len(train),'validation_rows':len(val),'train_last_target_available':int(train[-1]+h+1),
    'validation_last_target_available':int(val[-1]+h+1)}
   print('TRAINED',name,h,selected,flush=True)
 # No test metrics computed before this manifest is saved.
 save(out/'SELECTION.json',{'primary_method':'stabilized','validation_only':selection,'saved_before_test_scores':True})
 pd.concat(tables,ignore_index=True).to_parquet(out/'replay.parquet',index=False);check(s)
def evaluate(s):
 check(s);out=stage_out(s);selection=json.loads((out/'SELECTION.json').read_text())['validation_only'];replay=pd.read_parquet(out/'replay.parquet');results={}
 for key,sel in selection.items():
  name,hs=key.rsplit(':',1);h=int(hs);ids=np.arange(VAL_END,N-h)
  trace=replay[(replay.cohort==name)&(replay.horizon==h)];packs={}
  for method in trace.method.unique():
   r=trace[trace.method==method].sort_values('origin');assert len(r)==N
   packs[method]=(r.target.to_numpy(),r.raw.to_numpy(),r.corrected.to_numpy())
  r=trace[trace.method=='stabilized'].sort_values('origin');u=r.target.to_numpy();m=r['mean'].to_numpy()
  assert all(np.isfinite(p[2][ids]).all() for p in packs.values())
  subsets={'all':ids,'low_history':ids[m[ids]<1],'zero_history':ids[m[ids]==0],
   'zero_history_with_arrival':ids[(m[ids]==0)&(u[ids]>0)],'active_history':ids[m[ids]>=1]}
  strata={n:{method:metric(u,p[2],indices) for method,p in packs.items()} for n,indices in subsets.items()}
  a=strata['all']['stabilized'];b=strata['all'][sel['baseline']];gain=1-a['pinball']/b['pinball'] if b['pinball']>0 else None
  passed=gain is not None and gain>=.05 and .85<=a['coverage']<=.95
  daily={}
  for day in range(255,285):
   d=ids[(ids>=(day-225)*288)&(ids<(day-224)*288)]
   daily[str(day)]={method:metric(u,p[2],d) for method,p in packs.items()}
  results[key]={'gate':'RETROSPECTIVE_SPARSE_PASS' if passed else 'NO_GO','selected_baseline':sel['baseline'],
   'gain_vs_selected_equally_corrected_baseline':gain,'strata':strata,
   'raw_scores':{method:metric(u,p[1],ids) for method,p in packs.items()},'daily':daily}
  print(key,results[key]['gate'],gain,a['coverage'],flush=True)
 save(out/'results.json',{'comparisons':results,'new_models_trained':24,'operational_adoption':False,
  'all_conditions_pass':all(v['gate']=='RETROSPECTIVE_SPARSE_PASS' for v in results.values()),
  'source_previously_used_for_other_targets':True,'label':'recorded requests per5min, not actual complete traffic or GPU demand'})
 check(s)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','train','evaluate']);p.add_argument('--stage',type=Path,required=True)
 a=p.parse_args();globals()[a.mode](a.stage)
