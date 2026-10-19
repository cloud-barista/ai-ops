"""Manual histories, model refit and independent sorted-window feedback replay."""
import argparse,bisect,csv,hashlib,json,math
from collections import Counter,deque
from decimal import Decimal
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from burstgpt_sparse_calls import design as exact_feature_design
ROOT=Path('/home/researcher/ai-mcmp/research');N=60*288;START=225*288;TR=20*288;VE=30*288
COHORTS=('ChatGPT|API log','ChatGPT|Conversation log','GPT-4|API log','GPT-4|Conversation log')
PARAMS=dict(loss='quantile',quantile=.9,max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=20,l2_regularization=1,early_stopping=False,random_state=42)
FEATURES=('lag1_ratio','lag2_ratio','lag3_ratio','lag12_ratio','hour_max_ratio','hour_q90_ratio','recent_vs_older','log_hour_mean','hour_zero_fraction','bins_since_positive')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def q90(a):
 a=sorted(a);z=.9*(len(a)-1);i=math.floor(z);return a[i]+(z-i)*(a[min(i+1,len(a)-1)]-a[i])
def features(y):
 X=np.full((len(y),10),np.nan);mean=np.full(len(y),np.nan);maximum=mean.copy();quantile=mean.copy();scale=mean.copy();gap=0
 for i in range(1,len(y)):
  gap=1 if np.isfinite(y[i-1]) and y[i-1]>0 else min(288,gap+1) if np.isfinite(y[i-1]) else 0
  if i<12 or not np.isfinite(y[i-12:i]).all():continue
  past=y[i-12:i];m=sum(past)/12;s=max(m,1);mean[i]=m;scale[i]=s;maximum[i]=max(past);quantile[i]=q90(past)
  X[i]=[y[i-l]/s for l in (1,2,3,12)]+[max(past)/s,q90(past)/s,(sum(past[6:])/6)/max(sum(past[:6])/6,1),
   math.log1p(m),sum(past==0)/12,gap]
 return X,mean,scale,maximum,quantile
def peak(y,h):return np.array([max(y[i:i+h+1]) if np.isfinite(y[i:i+h+1]).all() else np.nan for i in range(len(y)-h)]+[np.nan]*h)
def correct(u,p,m,h):
 out=p.copy();ns=np.zeros(len(p),int);ds=np.zeros(len(p));ls=np.full(len(p),-1,int);history=deque();rank=[]
 for t in range(TR,len(p)):
  i=t-h-1
  if i>=TR and np.isfinite(u[i]) and np.isfinite(p[i]) and np.isfinite(m[i]) and m[i]>=1:
   v=float((u[i]-p[i])/m[i]);history.append((i,v));bisect.insort(rank,v)
  left=max(TR,t-h-288)
  while history and history[0][0]<left:
   _,v=history.popleft();j=bisect.bisect_left(rank,v);assert rank[j]==v;rank.pop(j)
  if not np.isfinite(p[t]) or not np.isfinite(m[t]) or m[t]<1:continue
  ns[t]=len(history)
  if history:ls[t]=history[-1][0]+h+1;assert ls[t]<=t
  if len(rank)>=96:
   z=.9*(len(rank)-1);j=math.floor(z);ds[t]=max(0.,rank[j]+(z-j)*(rank[min(j+1,len(rank)-1)]-rank[j]));out[t]+=m[t]*ds[t]
 return out,ns,ds,ls
def metric(u,p,ids):
 if not len(ids):return None
 e=u[ids]-p[ids]
 return {'rows':len(ids),'pinball':float(sum(.9*v if v>=0 else -.1*v for v in e)/len(ids)),
  'coverage':float(sum(u[i]<=p[i] for i in ids)/len(ids)),'mean_prediction':float(sum(p[ids])/len(ids)),'mean_target':float(sum(u[ids])/len(ids))}
def check_metric(x,y):
 if x is None:assert y is None;return
 for n,v in x.items():np.testing.assert_allclose(v,y[n],rtol=1e-10,atol=1e-10)
def main(s):
 out=s/'artifacts/burstgpt_sparse_calls_v1';frozen=json.loads((out/'FREEZE.json').read_text())
 for n,d in frozen['code'].items():assert sha(s/n)==d,n
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 refinement=json.loads((out/'VERIFIER_REFINEMENT.json').read_text())
 assert sha(Path(__file__))==refinement['new_verifier_sha256']
 hist=Counter();total=0
 with (ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv').open(newline='') as f:
  reader=csv.reader(f);next(reader)
  for row in reader:
   i=int(Decimal(row[0])//Decimal(300))-START
   if i>=N:break
   if i<0:continue
   hist[(row[3]+'|'+row[7],i)]+=1;total+=1
 saved=pd.read_parquet(out/'counts.parquet');audit=json.loads((ROOT/'data/external/burstgpt_v2/audit.json').read_text())
 mask=np.array([i*300>=audit['time']['timestamp_min_seconds'] and (i+1)*300<=audit['time']['timestamp_max_seconds'] for i in range(START,START+N)])
 np.testing.assert_array_equal(mask,saved.within_timestamp_extent)
 assert total==json.loads((out/'INPUT.json').read_text())['selected_rows'];np.testing.assert_array_equal(saved.relative_bin,np.arange(START,START+N))
 replay=pd.read_parquet(out/'replay.parquet');selection=json.loads((out/'SELECTION.json').read_text())['validation_only'];results=json.loads((out/'results.json').read_text())['comparisons']
 models=0;rows=0;conditions=0;days=0;mutations=0
 for name in COHORTS:
  y=np.array([hist[name,i] for i in range(N)],float);np.testing.assert_array_equal(y,saved[name]);y[~mask]=np.nan
  manual_X,mean,m,maximum,quantile=features(y);first=int(np.flatnonzero(mask)[0])+12
  exact=exact_feature_design(y);X=exact[list(FEATURES)].to_numpy()
  manual_known=np.isfinite(manual_X).all(axis=1);exact_known=np.isfinite(X).all(axis=1)
  np.testing.assert_array_equal(manual_known,exact_known)
  np.testing.assert_allclose(manual_X[manual_known],X[manual_known],rtol=1e-13,atol=1e-13)
  np.testing.assert_allclose(mean,exact['mean'],rtol=1e-13,atol=1e-13,equal_nan=True)
  np.testing.assert_allclose(m,exact.scale,rtol=1e-13,atol=1e-13,equal_nan=True)
  changed=y.copy();changed[VE:]=1e9;Xchange,*_=features(changed)
  np.testing.assert_allclose(manual_X[:VE+1],Xchange[:VE+1],rtol=0,atol=0,equal_nan=True)
  np.testing.assert_array_equal(X[:VE+1],exact_feature_design(changed)[list(FEATURES)].to_numpy()[:VE+1])
  for h in (1,2,6):
   u=peak(y,h);train=np.arange(first,TR-h);val=np.arange(TR,VE-h);ids=np.arange(VE,N-h);key=f'{name}:{h}'
   sel=selection[key];rec=results[key];raw={}
   assert sel['train_rows']==len(train) and sel['validation_rows']==len(val)
   assert sel['train_last_target_available']==TR and sel['validation_last_target_available']==VE
   for kind in ('stabilized','absolute'):
    path=out/(name.replace('|','_').replace(' ','_')+f'_h{h}_{kind}.joblib');assert sha(path)==sel['model_hashes'][path.name]
    b=joblib.load(path);assert tuple(b['features'])==FEATURES and b['params']==PARAMS
    np.testing.assert_array_equal(b['train_origins'],train)
    model=HistGradientBoostingRegressor(**PARAMS);z=u[train]/m[train] if kind=='stabilized' else u[train]
    w=m[train]/m[train].mean() if kind=='stabilized' else np.ones(len(train));model.fit(X[train],z,sample_weight=w)
    np.testing.assert_array_equal(model.predict(X),b['model'].predict(X))
    p=np.maximum(0,b['model'].predict(X));raw[kind]=p*m if kind=='stabilized' else p;models+=1
   ratios=u[train]/m[train];order=np.argsort(ratios,kind='stable');cut=np.searchsorted(np.cumsum(m[train][order]),.9*sum(m[train]))
   raw['train_weighted_q90']=ratios[order[min(cut,len(order)-1)]]*m
   raw['past_hour_max']=maximum;raw['past_hour_q90']=quantile;raw['past_hour_mean']=mean.copy();corr={}
   known=np.isfinite(X).all(axis=1)&np.isfinite(m)
   for method,p in raw.items():
    p[~known]=np.nan;q,n,d,l=correct(u,p,m,h);corr[method]=q
    r=replay[(replay.cohort==name)&(replay.horizon==h)&(replay.method==method)].sort_values('origin')
    np.testing.assert_array_equal(r.origin,np.arange(N))
    for col,a in [('raw',p),('corrected',q),('target',u),('mean',mean),('scale',m),('delta',d)]:np.testing.assert_allclose(r[col],a,rtol=1e-10,atol=1e-10,equal_nan=True)
    np.testing.assert_array_equal(r.feedback_count,n);np.testing.assert_array_equal(r.last_available,l);rows+=len(r)
    check_metric(metric(u,q,val),sel['validation'][method]);check_metric(metric(u,p,ids),rec['raw_scores'][method])
   chosen=min([n for n in raw if n!='stabilized'],key=lambda n:metric(u,corr[n],val)['pinball'])
   assert chosen==sel['baseline']==rec['selected_baseline']
   subsets={'all':ids,'low_history':ids[mean[ids]<1],'zero_history':ids[mean[ids]==0],
    'zero_history_with_arrival':ids[(mean[ids]==0)&(u[ids]>0)],'active_history':ids[mean[ids]>=1]}
   for label,ii in subsets.items():
    for method,p in corr.items():check_metric(metric(u,p,ii),rec['strata'][label][method])
   for day in range(255,285):
    d=ids[(ids>=(day-225)*288)&(ids<(day-224)*288)]
    for method,p in corr.items():check_metric(metric(u,p,d),rec['daily'][str(day)][method]);days+=1
   a=metric(u,corr['stabilized'],ids);b=metric(u,corr[chosen],ids);gain=1-a['pinball']/b['pinball'] if b['pinball']>0 else None
   if gain is None:assert rec['gain_vs_selected_equally_corrected_baseline'] is None
   else:np.testing.assert_allclose(gain,rec['gain_vs_selected_equally_corrected_baseline'],rtol=1e-10,atol=1e-10)
   assert rec['gate']==('RETROSPECTIVE_SPARSE_PASS' if gain is not None and gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO')
   changed_u=peak(changed,h);np.testing.assert_allclose(correct(changed_u,raw['stabilized'],m,h)[0][:VE+1],corr['stabilized'][:VE+1],rtol=0,atol=0,equal_nan=True)
   conditions+=1;mutations+=1;print('VERIFIED_CONDITION',name,h,flush=True)
 assert models==24 and rows==len(replay)==1244160 and conditions==12 and days==2160
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 with (out/'verification.json').open('x') as f:json.dump({'status':'VERIFIED','independent_decimal_counts':total,
  'models_refitted':models,'replay_rows_checked':rows,'condition_gates_checked':conditions,'daily_method_metrics_checked':days,
  'future_mutation_checks':mutations,'source_and_helpers_unchanged':True,
  'manual_features_checked_on_complete_histories_rtol':1e-13,'refit_uses_exact_production_feature_arithmetic':True,
  'exact_saved_refit_predictions_match':True,'initial_refit_failure_preserved':True},f,indent=2)
 print('VERIFIED',models,'models',rows,'rows',flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);main(p.parse_args().stage)
