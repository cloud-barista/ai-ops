"""Fixed seven-feature donor models and new-source recorded-call transfer."""
import argparse,csv,hashlib,json,datetime
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from train_evaluate_delay_capacity import PARAMS
from train_copilot_call_demand import design
from copilot_call_feedback import correct_completed
ROOT=Path('/home/researcher/ai-mcmp/research')
PARENT=ROOT/'artifacts/copilot_call_demand_v2'
FEATURES=('lag1_over_scale','lag2_over_scale','lag3_over_scale','lag12_over_scale',
 'past_hour_max_over_scale','past_hour_q90_over_scale','recent_six_over_previous_six')
METHODS=('hgb','train_weighted_peak_q90','past_hour_max','past_hour_q90','past_hour_mean')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False);f.write('\n')
def target(y,h):return np.r_[np.max(np.lib.stride_tricks.sliding_window_view(y,h+1),axis=1),np.full(h,np.nan)]
def score(u,p,ids):
 e=u[ids]-p[ids]
 assert np.isfinite(e).all()
 return {'rows':len(ids),'pinball':float(np.maximum(.9*e,-.1*e).mean()),'coverage':float(np.mean(u[ids]<=p[ids]))}
def qtrain(y,m,h):
 tr=np.arange(12,864-h);r=target(y,h)[tr]/m[tr];order=np.argsort(r,kind='stable');w=m[tr][order]
 i=min(int(np.searchsorted(np.cumsum(w),.9*w.sum())),len(order)-1)
 return float(r[order[i]])
def bases(f,q):return {'train_weighted_peak_q90':q*f.scale.to_numpy(),
 'past_hour_max':f.hour_max.to_numpy(),'past_hour_q90':f.hour_q90.to_numpy(),'past_hour_mean':f.scale.to_numpy()}


def prepare(s):
 out=s/'artifacts/copilot_external2023_v1';out.mkdir(parents=True,exist_ok=False)
 metadata=json.loads((s/'reports/azure2023_data_directory_metadata.json').read_text())
 names=('AzureLLMInferenceTrace_code.csv','AzureLLMInferenceTrace_conv.csv')
 assets=[next(a for a in metadata if a['name']==n) for n in names]
 assert sum(a['size'] for a in assets)<=2000000
 inputs=[PARENT/'counts.parquet',PARENT/'verification.json',ROOT/'scripts/train_copilot_call_demand.py',
 ROOT/'scripts/train_evaluate_delay_capacity.py',ROOT/'scripts/copilot_call_feedback.py']
 assert json.loads(inputs[1].read_text())['status']=='VERIFIED'
 code=[Path(__file__),s/'tests/test_copilot_external2023.py',s/'reports/copilot_external2023_protocol_v1.json']
 save(out/'FREEZE.json',{'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'assets':[{k:a[k] for k in ('name','size','sha','download_url')} for a in assets],
  'inputs':{str(p):sha(p) for p in inputs},'code':{str(p.relative_to(s)):sha(p) for p in code},
  'params':PARAMS,'features':FEATURES,'protocol':json.loads(code[-1].read_text()),
  'frozen_before_external_download_and_new_fit':True})


def check(s):
 f=json.loads((s/'artifacts/copilot_external2023_v1/FREEZE.json').read_text())
 for n,d in f['inputs'].items():assert sha(Path(n))==d,n
 for n,d in f['code'].items():assert sha(s/n)==d,n
 return f


def train(s):
 check(s);out=s/'artifacts/copilot_external2023_v1'
 y=pd.read_parquet(PARENT/'counts.parquet').recorded_calls.to_numpy(float)
 f=design(y);m=f.scale.to_numpy();X=f[list(FEATURES)].to_numpy();selection={}
 for h in (1,2,6):
  u=target(y,h);tr=np.arange(12,864-h);val=np.arange(864,1152-h)
  model=HistGradientBoostingRegressor(**PARAMS).fit(X[tr],u[tr]/m[tr],sample_weight=m[tr]/m[tr].mean())
  q=qtrain(y,m,h);p=out/f'no_calendar_delay{h}_q90.joblib'
  joblib.dump({'model':model,'features':FEATURES,'params':PARAMS,'delay':h,'train_q':q,'training_origins':tr},p,compress=3)
  raw={'hgb':np.maximum(0,model.predict(X))*m,**bases(f,q)}
  corrected={n:correct_completed(u,v,m,h)[0] for n,v in raw.items()}
  chosen=min(METHODS[1:],key=lambda n:score(u,corrected[n],val)['pinball'])
  selection[str(h)]={'model_sha256':sha(p),'baseline':chosen,'training_rows':len(tr),
   'validation_scores':{n:score(u,v,val) for n,v in corrected.items()}}
 save(out/'DONOR_SELECTION.json',selection);check(s)
 print('TRAINED and fixed donor selection before external raw inspection')


def aggregate(p):
 stamps=[]
 with p.open(newline='') as f:
  reader=csv.DictReader(f);assert set(reader.fieldnames)=={'TIMESTAMP','ContextTokens','GeneratedTokens'}
  for row in reader:
   # Preserve recorded wall clock. No UTC conversion or calendar feature is used.
   stamps.append(pd.Timestamp(row['TIMESTAMP']).tz_localize(None))
 assert stamps and all(x.date()==stamps[0].date() for x in stamps)
 anchor=pd.Timestamp(stamps[0].date())
 counts=np.zeros(288,dtype=np.int64)
 for t in stamps:
  i=int((t.value-anchor.value)//(300*10**9));assert 0<=i<288;counts[i]+=1
 return counts,{'rows':len(stamps),'first_recorded_timestamp':min(stamps).isoformat(),
  'last_recorded_timestamp':max(stamps).isoformat(),'recorded_date':str(stamps[0].date()),
  'timezone_unspecified':True,'zero_recorded_bins':int(np.sum(counts==0)),'sha256':sha(p)}


def evaluate(s):
 freeze=check(s);out=s/'artifacts/copilot_external2023_v1';selection=json.loads((out/'DONOR_SELECTION.json').read_text())
 audits={};results={};rows=[]
 for asset in freeze['assets']:
  p=s/'data'/asset['name'];b=p.read_bytes()
  assert len(b)==asset['size'] and hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==asset['sha']
  y,a=aggregate(p);audits[p.name]=a
  f=design(y);m=f.scale.to_numpy();X=f[list(FEATURES)].to_numpy()
  for h in (1,2,6):
   bundle=joblib.load(out/f'no_calendar_delay{h}_q90.joblib')
   assert sha(out/f'no_calendar_delay{h}_q90.joblib')==selection[str(h)]['model_sha256']
   u=target(y,h);raw={'hgb':np.maximum(0,bundle['model'].predict(X))*m,**bases(f,bundle['train_q'])}
   ids=np.arange(120,len(y)-h);supported=np.isfinite(m[ids])&(m[ids]>=1)
   for v in raw.values():supported &= np.isfinite(v[ids])
   support={'possible_scored_origins':len(ids),'supported_origins':int(supported.sum()),'supported_fraction':float(supported.mean())}
   key=f'{p.stem}:{h}'
   if supported.sum()<144 or supported.mean()<.8:
    results[key]={'support':support,'gate':'SOURCE_SUPPORT_NO_GO'};continue
   ids=ids[supported];corrected={}
   for n,v in raw.items():
    corrected[n],count,delta,last=correct_completed(u,v,m,h,first_origin=12)
    rows.append(pd.DataFrame({'source':p.name,'delay':h,'method':n,'origin':np.arange(len(y)),
     'calls':y,'scale':m,'target':u,'raw':v,'corrected':corrected[n],
     'feedback_count':count,'delta':delta,'last_feedback_available':last}))
   scores={n:score(u,v,ids) for n,v in corrected.items()};chosen=selection[str(h)]['baseline']
   a,b=scores['hgb'],scores[chosen];gain=1-a['pinball']/b['pinball']
   results[key]={'support':support,'scores':scores,'raw_scores':{n:score(u,v,ids) for n,v in raw.items()},
    'fixed_donor_baseline':chosen,'gain_vs_same_feedback_baseline':gain,
    'gate':'TRANSFER_PASS' if gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO'}
 save(out/'source_audit.json',audits)
 if rows:pd.concat(rows,ignore_index=True).to_parquet(out/'external_replay.parquet',index=False)
 save(out/'results.json',{'comparisons':results,'overall_transfer_pass':all(v['gate']=='TRANSFER_PASS' for v in results.values()),
  'operational_adoption':False,'new_raw_external_source_before_scoring':True,'single_day_per_service':True})
 check(s);print(json.dumps(results,indent=2))


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','train','evaluate']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
 {'prepare':prepare,'train':train,'evaluate':evaluate}[a.mode](a.stage)
