"""Independent Decimal timestamp counts and explicit-ID completed feedback."""
import argparse,csv,hashlib,json,math
from collections import Counter
from decimal import Decimal
from pathlib import Path
import joblib,numpy as np,pandas as pd
from train_copilot_call_demand import design
ROOT=Path('/home/researcher/ai-mcmp/research');START=225*288;N=30*288
COHORTS=(('ChatGPT','API log'),('ChatGPT','Conversation log'),('GPT-4','API log'),('GPT-4','Conversation log'))

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def peak(y,h):return np.array([max(y[i:i+h+1]) if np.isfinite(y[i:i+h+1]).all() else np.nan for i in range(len(y)-h)]+[np.nan]*h)
def quantile(v):
 a=sorted(v);z=.9*(len(a)-1);i=math.floor(z)
 return a[i]+(z-i)*(a[min(i+1,len(a)-1)]-a[i])
def reference(u,p,m,h,first):
 out=p.copy();count=np.zeros(len(p),dtype=int);delta=np.zeros(len(p));last=np.full(len(p),-1,dtype=int)
 for t in range(first,len(p)):
  if not np.isfinite(p[t]) or p[t]<0 or not np.isfinite(m[t]) or m[t]<1:continue
  ids=[i for i in range(max(first,t-h-288),t-h) if np.isfinite(u[i]) and u[i]>=0 and np.isfinite(p[i]) and p[i]>=0 and np.isfinite(m[i]) and m[i]>=1]
  count[t]=len(ids)
  if ids:last[t]=ids[-1]+h+1
  assert not ids or last[t]<=t
  if len(ids)>=96:
   delta[t]=max(0.,quantile([(u[i]-p[i])/m[i] for i in ids]));out[t]+=m[t]*delta[t]
 return out,count,delta,last

def metric(u,p,ids):
 e=[u[i]-p[i] for i in ids]
 return {'rows':len(ids),'pinball':sum(.9*v if v>=0 else -.1*v for v in e)/len(ids),'coverage':sum(u[i]<=p[i] for i in ids)/len(ids)}
def check_metric(got,saved):
 for n,v in got.items():np.testing.assert_allclose(v,saved[n],rtol=1e-11,atol=1e-11)

def main(s):
 out=s/'artifacts/burstgpt_call_transfer_v1';frozen=json.loads((out/'FREEZE.json').read_text())
 for n,d in frozen['code'].items():assert sha(s/n)==d,n
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 audit=json.loads((ROOT/'data/external/burstgpt_v2/audit.json').read_text());hist=Counter();total=0
 with (ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv').open(newline='') as f:
  r=csv.reader(f);columns=next(r);assert columns==audit['source']['columns']
  for row in r:
   t=Decimal(row[0]);idx=int(t//Decimal(300))-START
   if idx>=N:break
   if idx<0:continue
   hist[(row[3],row[7],idx)]+=1;total+=1
 savedcounts=pd.read_parquet(out/'counts.parquet')
 np.testing.assert_array_equal(savedcounts.relative_bin,np.arange(START,START+N))
 mask=np.array([i*300>=audit['time']['timestamp_min_seconds'] and (i+1)*300<=audit['time']['timestamp_max_seconds'] for i in range(START,START+N)])
 np.testing.assert_array_equal(savedcounts.within_recorded_extent,mask)
 assert total==json.loads((out/'source_audit.json').read_text())['selected_rows']
 selection=json.loads((out/'SELECTION.json').read_text())['validation_only'];results=json.loads((out/'results.json').read_text())['comparisons']
 replay=pd.read_parquet(out/'replay.parquet');rows=0;scored=0;mutation_checks=0
 for pair in COHORTS:
  name='|'.join(pair);y=np.array([hist[(pair[0],pair[1],i)] for i in range(N)],float)
  np.testing.assert_array_equal(y,savedcounts[name]);y[~mask]=np.nan
  frame=design(y);m=frame.scale.to_numpy();first=int(np.flatnonzero(mask)[0])+12
  for i in range(12,N):
   if np.isfinite(y[i-12:i]).all():np.testing.assert_allclose(m[i],sum(y[i-12:i])/12,rtol=1e-12)
   else:assert not np.isfinite(m[i])
  for h in (1,2,6):
   bundle=joblib.load(ROOT/'artifacts/copilot_external2023_v1'/f'no_calendar_delay{h}_q90.joblib')
   u=peak(y,h);raw={'hgb':np.maximum(0,bundle['model'].predict(frame[list(bundle['features'])].to_numpy()))*m,
    'train_weighted_peak_q90':bundle['train_q']*m}
   for n,fn in [('past_hour_max',max),('past_hour_q90',quantile),('past_hour_mean',lambda a:sum(a)/len(a))]:
    raw[n]=np.array([fn(list(y[i-12:i])) if i>=12 and np.isfinite(y[i-12:i]).all() else np.nan for i in range(N)])
   for p in raw.values():p[~np.isfinite(m)|(m<1)]=np.nan
   corrected={}
   for n,p in raw.items():
    corrected[n],count,delta,last=reference(u,p,m,h,first)
    saved=replay[(replay.cohort==name)&(replay.horizon==h)&(replay.method==n)].sort_values('origin')
    np.testing.assert_array_equal(saved.origin,np.arange(N));np.testing.assert_allclose(saved.target,u,rtol=0,atol=0,equal_nan=True)
    for column,actual in [('raw',p),('corrected',corrected[n]),('scale',m),('delta',delta)]:
     np.testing.assert_allclose(saved[column],actual,rtol=1e-11,atol=1e-11,equal_nan=True)
    np.testing.assert_array_equal(saved.feedback_count,count);np.testing.assert_array_equal(saved.last_available,last);rows+=len(saved)
   ids={};support={}
   for phase,begin,end in [('validation',first,288-h),('test',288,N-h)]:
    candidates=list(range(begin,end));valid=[i for i in candidates if np.isfinite(u[i]) and np.isfinite(m[i]) and m[i]>=1 and all(np.isfinite(p[i]) and p[i]>=0 for p in raw.values())]
    ids[phase]=valid;support[phase]={'possible_origins':len(candidates),'supported_origins':len(valid),'supported_fraction':len(valid)/len(candidates)}
   key=f'{name}:{h}';sel=selection[key];rec=results[key]
   for phase in support:
    for n,v in support[phase].items():np.testing.assert_allclose(v,sel[phase+'_support'][n],rtol=1e-12)
   enough=all(v['supported_origins']>=144 and v['supported_fraction']>=.8 for v in support.values())
   if not enough:
    assert sel['gate']==rec['gate']=='SOURCE_SUPPORT_NO_GO' and 'scores' not in rec
   else:
    chosen=min(list(raw)[1:],key=lambda n:metric(u,corrected[n],ids['validation'])['pinball'])
    assert chosen==sel['baseline']==rec['chosen_baseline'];scored+=1
    for n,p in raw.items():
     check_metric(metric(u,corrected[n],ids['validation']),sel['validation_scores'][n])
     check_metric(metric(u,corrected[n],ids['test']),rec['scores'][n]);check_metric(metric(u,p,ids['test']),rec['raw_scores'][n])
     for day in range(226,255):
      d=[i for i in ids['test'] if (day-225)*288<=i<(day-224)*288]
      assert len(d)==rec['daily'][str(day)]['supported_origins']
      if d:check_metric(metric(u,corrected[n],d),rec['daily'][str(day)]['scores'][n])
    a=metric(u,corrected['hgb'],ids['test']);b=metric(u,corrected[chosen],ids['test']);gain=1-a['pinball']/b['pinball']
    np.testing.assert_allclose(gain,rec['gain_vs_equally_corrected_baseline'],rtol=1e-11)
    assert rec['gate']==('RETROSPECTIVE_TRANSFER_PASS' if gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO')
   changed=y.copy();changed[288:]=10**9;newu=peak(changed,h)
   np.testing.assert_allclose(reference(newu,raw['hgb'],m,h,first)[0][:289],corrected['hgb'][:289],rtol=0,atol=0,equal_nan=True);mutation_checks+=1
 assert rows==len(replay)==518400
 for n,d in frozen['inputs'].items():assert sha(Path(n))==d,n
 with (out/'verification.json').open('x') as f:json.dump({'status':'VERIFIED','independent_decimal_counts':True,'replay_rows':rows,
  'scored_conditions':scored,'support_gates_checked':12,'daily_metrics_checked':True,'future_mutation_checks':mutation_checks,'frozen_inputs_unchanged':True},f,indent=2)
 print('VERIFIED',rows,'rows',scored,'scored conditions')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True,type=Path);a=p.parse_args();main(a.stage)
