"""Refit donor models and independently count external timestamps and feedback."""
from pathlib import Path
import argparse,csv,datetime,json,math,hashlib
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from train_copilot_call_demand import design
ROOT=Path('/home/researcher/ai-mcmp/research')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def peak(y,h):return np.array([max(y[i:i+h+1]) if i+h<len(y) else np.nan for i in range(len(y))])
def q90(vals):
 v=sorted(vals);pos=.9*(len(v)-1);j=math.floor(pos)
 return v[j]+(pos-j)*(v[min(j+1,len(v)-1)]-v[j])
def ref_feedback(u,p,m,h,start):
 out=p.copy();count=np.zeros(len(p),dtype=int);delta=np.zeros(len(p));last=np.full(len(p),-1,dtype=int)
 for t in range(start,len(p)):
  ids=[i for i in range(max(start,t-h-288),t-h) if math.isfinite(u[i]) and u[i]>=0 and math.isfinite(p[i]) and p[i]>=0 and math.isfinite(m[i]) and m[i]>=1]
  if not math.isfinite(p[t]) or p[t]<0 or not math.isfinite(m[t]) or m[t]<1:continue
  assert all(i+h+1<=t for i in ids)
  count[t]=len(ids)
  if ids:last[t]=ids[-1]+h+1
  if len(ids)>=96:
   delta[t]=max(0,q90([(u[i]-p[i])/m[i] for i in ids]));out[t]+=m[t]*delta[t]
 return out,count,delta,last

def metrics(u,p,ids):
 errors=[u[i]-p[i] for i in ids]
 return {'rows':len(ids),'pinball':sum(.9*e if e>=0 else -.1*e for e in errors)/len(ids),
 'coverage':sum(u[i]<=p[i] for i in ids)/len(ids)}
def reference_counts(p):
 counts=np.zeros(288,dtype=int);day=None;n=0
 with p.open(newline='') as f:
  for row in csv.DictReader(f):
   t=datetime.datetime.strptime(row['TIMESTAMP'][:19].replace('T',' '),'%Y-%m-%d %H:%M:%S')
   if day is None:day=t.date()
   assert t.date()==day
   counts[t.hour*12+t.minute//5]+=1;n+=1
 return counts,n

def reference_bases(y,m,q):
 arrays={'train_weighted_peak_q90':q*m}
 for name,fn in [('past_hour_max',max),('past_hour_q90',q90),('past_hour_mean',lambda a:sum(a)/len(a))]:
  arrays[name]=np.array([fn(list(y[i-12:i])) if i>=12 else np.nan for i in range(len(y))])
 return arrays

def main(s):
 out=s/'artifacts/copilot_external2023_v1';freeze=json.loads((out/'FREEZE.json').read_text())
 vf=json.loads((out/'VERIFIER_FREEZE.json').read_text())
 fix=json.loads((out/'VERIFIER_FAILURE_HANDLING.json').read_text())
 assert sha(Path(__file__))==fix['verifier_sha256']
 for n,d in vf['raw_sha256'].items():assert sha(s/'data'/n)==d,n
 for n,d in freeze['inputs'].items():assert sha(Path(n))==d,n
 for n,d in freeze['code'].items():assert sha(s/n)==d,n
 donor=pd.read_parquet(ROOT/'artifacts/copilot_call_demand_v2/counts.parquet').recorded_calls.to_numpy(float)
 f=design(donor);m=f.scale.to_numpy();features=freeze['features'];X=f[features].to_numpy()
 select=json.loads((out/'DONOR_SELECTION.json').read_text());models={}
 for h in (1,2,6):
  u=peak(donor,h);tr=np.arange(12,864-h);val=np.arange(864,1152-h)
  p=out/f'no_calendar_delay{h}_q90.joblib';bundle=joblib.load(p)
  assert sha(p)==select[str(h)]['model_sha256']
  np.testing.assert_array_equal(bundle['training_origins'],tr);assert list(bundle['features'])==features
  fresh=HistGradientBoostingRegressor(**freeze['params']).fit(X[tr],u[tr]/m[tr],sample_weight=m[tr]/m[tr].mean())
  np.testing.assert_array_equal(fresh.predict(X),bundle['model'].predict(X))
  pairs=sorted((u[i]/m[i],m[i]) for i in tr);total=0;cut=.9*sum(w for q,w in pairs)
  for q,w in pairs:
   total+=w
   if total>=cut:break
  np.testing.assert_equal(q,bundle['train_q'])
  raw={'hgb':np.maximum(0,fresh.predict(X))*m,**reference_bases(donor,m,q)}
  preds={n:ref_feedback(u,v,m,h,864)[0] for n,v in raw.items()}
  chosen=min(list(raw)[1:],key=lambda n:metrics(u,preds[n],val)['pinball'])
  assert chosen==select[str(h)]['baseline'];models[h]=bundle
 replay=pd.read_parquet(out/'external_replay.parquet') if (out/'external_replay.parquet').exists() else pd.DataFrame();audit=json.loads((out/'source_audit.json').read_text())
 results=json.loads((out/'results.json').read_text())['comparisons'];rows=0;mutations=0;scored=0
 for asset in freeze['assets']:
  p=s/'data'/asset['name'];b=p.read_bytes()
  assert len(b)==asset['size'] and hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==asset['sha']
  y,n=reference_counts(p);assert n==audit[p.name]['rows']
  f=design(y);m=f.scale.to_numpy();X=f[features].to_numpy()
  for i in range(12,288):np.testing.assert_allclose(m[i],sum(y[i-12:i])/12,rtol=1e-12)
  for h,bundle in models.items():
   u=peak(y,h);raw={'hgb':np.maximum(0,bundle['model'].predict(X))*m,**reference_bases(y,m,bundle['train_q'])}
   ids=np.arange(120,288-h);mask=np.isfinite(m[ids])&(m[ids]>=1)
   for v in raw.values():mask &= np.isfinite(v[ids])
   key=f'{p.stem}:{h}';record=results[key]
   assert record['support']['supported_origins']==int(mask.sum())
   if mask.sum()<144 or mask.mean()<.8:
    assert record['gate']=='SOURCE_SUPPORT_NO_GO';continue
   ids=ids[mask];preds={};scored+=1
   for name,v in raw.items():
    pred,count,delta,last=ref_feedback(u,v,m,h,12);preds[name]=pred
    saved=replay[(replay.source==p.name)&(replay.delay==h)&(replay.method==name)].sort_values('origin')
    np.testing.assert_array_equal(saved.origin,np.arange(288));np.testing.assert_array_equal(saved.calls,y)
    np.testing.assert_allclose(saved.target,u,rtol=0,atol=0,equal_nan=True)
    np.testing.assert_allclose(saved.raw,v,rtol=1e-12,atol=1e-12,equal_nan=True)
    np.testing.assert_allclose(saved.corrected,pred,rtol=1e-12,atol=1e-12,equal_nan=True)
    np.testing.assert_array_equal(saved.feedback_count,count);np.testing.assert_allclose(saved.delta,delta,rtol=1e-12,atol=1e-12)
    np.testing.assert_array_equal(saved.last_feedback_available,last);rows+=len(saved)
    for name2,pred2 in [('scores',pred),('raw_scores',v)]:
     for metric,value in metrics(u,pred2,ids).items():np.testing.assert_allclose(value,record[name2][name][metric],rtol=1e-12,atol=1e-12)
   chosen=select[str(h)]['baseline'];assert chosen==record['fixed_donor_baseline']
   a=metrics(u,preds['hgb'],ids);b=metrics(u,preds[chosen],ids);gain=1-a['pinball']/b['pinball']
   np.testing.assert_allclose(gain,record['gain_vs_same_feedback_baseline'],rtol=1e-12)
   assert record['gate']==('TRANSFER_PASS' if gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO')
   changed=y.copy();changed[160:]=10**9;newu=peak(changed,h);mutations+=1
   np.testing.assert_allclose(ref_feedback(newu,raw['hgb'],m,h,12)[0][:161],preds['hgb'][:161],rtol=0,atol=0,equal_nan=True)
 assert len(replay)==rows
 for n,d in freeze['inputs'].items():assert sha(Path(n))==d,n
 with (out/'verification.json').open('x') as f:json.dump({'status':'VERIFIED','refitted_donor_models':3,
 'independent_csv_counts':True,'replay_rows_verified':rows,'source_support_and_all_metrics_gates_checked':True,
 'scored_external_comparisons':scored,'external_future_mutation_checks':mutations,'frozen_inputs_unchanged':True},f,indent=2)
 print('VERIFIED',rows,'rows')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();main(a.stage)
