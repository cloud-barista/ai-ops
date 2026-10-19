"""Fixed Copilot recorded-call predictors on bounded BurstGPT request histories."""
import argparse,csv,datetime,hashlib,json,math
from pathlib import Path
import joblib,numpy as np,pandas as pd
from copilot_call_feedback import correct_completed
from train_copilot_call_demand import design
ROOT=Path('/home/researcher/ai-mcmp/research')
RAW=ROOT/'data/external/burstgpt_v2/BurstGPT_3.csv'
AUDIT=ROOT/'data/external/burstgpt_v2/audit.json'
MODEL=ROOT/'artifacts/copilot_external2023_v1'
COHORTS=(('ChatGPT','API log'),('ChatGPT','Conversation log'),('GPT-4','API log'),('GPT-4','Conversation log'))
START=225*288;N=30*288
METHODS=('hgb','train_weighted_peak_q90','past_hour_max','past_hour_q90','past_hour_mean')

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False);f.write('\n')
def prepare(s):
 out=s/'artifacts/burstgpt_call_transfer_v1';out.mkdir(parents=True,exist_ok=False)
 a=json.loads(AUDIT.read_text());assert RAW.stat().st_size==a['source']['size_bytes'] and sha(RAW)==a['source']['sha256']
 inputs=[RAW,AUDIT,MODEL/'verification.json',MODEL/'DONOR_SELECTION.json',MODEL/'FREEZE.json']+list(MODEL.glob('*.joblib'))
 inputs += [ROOT/'scripts'/n for n in ('copilot_call_feedback.py','train_copilot_call_demand.py','train_evaluate_delay_capacity.py','delay_capacity_policy.py','demand_capacity_policy.py')]
 assert json.loads((MODEL/'verification.json').read_text())['refitted_donor_models']==3
 code=[Path(__file__),s/'scripts/verify_burstgpt_call_transfer.py',s/'tests/test_burstgpt_call_transfer.py',s/'reports/burstgpt_call_transfer_protocol_v1.json']
 save(out/'FREEZE.json',{'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'inputs':{str(p):sha(p) for p in inputs},
  'code':{str(p.relative_to(s)):sha(p) for p in code},'protocol':json.loads(code[-1].read_text()),'these_fixed_models_external_call_count_scores_not_yet_computed':True})
def check(s):
 f=json.loads((s/'artifacts/burstgpt_call_transfer_v1/FREEZE.json').read_text())
 for n,d in f['inputs'].items():assert sha(Path(n))==d,n
 for n,d in f['code'].items():assert sha(s/n)==d,n

def extent_mask(first_seconds,last_seconds,start_bin=START,n=N):
 ids=np.arange(start_bin,start_bin+n)
 # Both edges lie inside the timestamp extent. This is not a heartbeat proof.
 return (ids*300>=first_seconds)&((ids+1)*300<=last_seconds)

def targets(y,h):
 return np.r_[np.max(np.lib.stride_tricks.sliding_window_view(y,h+1),axis=1),np.full(h,np.nan)]
def score(u,p,ids):
 e=u[ids]-p[ids];assert np.isfinite(e).all()
 return {'rows':len(ids),'pinball':float(np.maximum(.9*e,-.1*e).mean()),'coverage':float(np.mean(u[ids]<=p[ids]))}
def extract(s):
 counts=np.zeros((len(COHORTS),N),dtype=np.int64);pos={pair:i for i,pair in enumerate(COHORTS)};total=0;previous=-math.inf
 with RAW.open(newline='') as f:
  r=csv.DictReader(f);assert r.fieldnames==json.loads(AUDIT.read_text())['source']['columns']
  for row in r:
   t=float(row['Timestamp']);assert math.isfinite(t) and t>=previous;previous=t
   if t>=(START+N)*300:break
   if t<START*300:continue
   c=pos[(row['Model'],row['Log Type'])];b=int(t//300)-START;counts[c,b]+=1;total+=1
 a=json.loads(AUDIT.read_text());assert total==sum(a['by_day'][str(d)]['parsed'] for d in range(225,255))
 mask=extent_mask(a['time']['timestamp_min_seconds'],a['time']['timestamp_max_seconds'])
 pd.DataFrame({'relative_bin':np.arange(START,START+N),'within_recorded_extent':mask,
  **{'|'.join(pair):counts[i] for i,pair in enumerate(COHORTS)}}).to_parquet(s/'artifacts/burstgpt_call_transfer_v1/counts.parquet',index=False)
 save(s/'artifacts/burstgpt_call_transfer_v1/source_audit.json',{'selected_rows':total,'relative_days':[225,254],
  'full_bins_within_timestamp_extent':int(mask.sum()),'outside_extent_bins':int((~mask).sum()),
  'cohort_rows':{'|'.join(pair):int(counts[i].sum()) for i,pair in enumerate(COHORTS)},'recorded_empty_not_actual_zero':True})
 return counts,mask

def common_ids(u,m,raw,start,end):
 ids=np.arange(start,end);valid=np.isfinite(u[ids])&np.isfinite(m[ids])&(m[ids]>=1)
 for p in raw.values():valid &= np.isfinite(p[ids])&(p[ids]>=0)
 return ids[valid],{'possible_origins':len(ids),'supported_origins':int(valid.sum()),'supported_fraction':float(valid.mean())}
def supported(a):return a['supported_origins']>=144 and a['supported_fraction']>=.8

def run(s):
 check(s);out=s/'artifacts/burstgpt_call_transfer_v1';counts,mask=extract(s);selection={};packs={};tables=[]
 model_selection=json.loads((MODEL/'DONOR_SELECTION.json').read_text())
 for ci,pair in enumerate(COHORTS):
  name='|'.join(pair);y=counts[ci].astype(float);y[~mask]=np.nan
  f=design(y);m=f.scale.to_numpy();first=int(np.flatnonzero(mask)[0])+12
  for h in (1,2,6):
   path=MODEL/f'no_calendar_delay{h}_q90.joblib';assert sha(path)==model_selection[str(h)]['model_sha256']
   bundle=joblib.load(path);X=f[list(bundle['features'])].to_numpy();u=targets(y,h)
   raw={'hgb':np.maximum(0,bundle['model'].predict(X))*m,'train_weighted_peak_q90':bundle['train_q']*m,
    'past_hour_max':f.hour_max.to_numpy(),'past_hour_q90':f.hour_q90.to_numpy(),'past_hour_mean':m.copy()}
   # Unsupported present or historical scales abstain; future targets do not enter forecasts.
   for p in raw.values():p[~np.isfinite(m)|(m<1)]=np.nan
   corrected={}
   for method,p in raw.items():
    q,n,delta,last=correct_completed(u,p,m,h,first_origin=first);corrected[method]=q
    tables.append(pd.DataFrame({'cohort':name,'horizon':h,'method':method,'origin':np.arange(N),
     'target':u,'raw':p,'corrected':q,'scale':m,'feedback_count':n,'delta':delta,'last_available':last}))
   val,vs=common_ids(u,m,raw,first,288-h);test,ts=common_ids(u,m,raw,288,N-h);key=f'{name}:{h}'
   if not supported(vs) or not supported(ts):
    selection[key]={'gate':'SOURCE_SUPPORT_NO_GO','validation_support':vs,'test_support':ts};continue
   chosen=min(METHODS[1:],key=lambda n:score(u,corrected[n],val)['pinball'])
   selection[key]={'baseline':chosen,'validation_support':vs,'test_support':ts,
    'validation_scores':{n:score(u,v,val) for n,v in corrected.items()}}
   packs[key]=(h,u,raw,corrected,chosen,test)
 save(out/'SELECTION.json',{'validation_only':selection,'saved_before_test_scores':True})
 pd.concat(tables,ignore_index=True).to_parquet(out/'replay.parquet',index=False)
 results={k:v for k,v in selection.items() if v.get('gate')=='SOURCE_SUPPORT_NO_GO'}
 for key,(h,u,raw,corrected,chosen,ids) in packs.items():
  scores={n:score(u,p,ids) for n,p in corrected.items()};gain=1-scores['hgb']['pinball']/scores[chosen]['pinball']
  cov=scores['hgb']['coverage'];daily={}
  for day in range(226,255):
   d=ids[(ids>=(day-225)*288)&(ids<(day-224)*288)]
   daily[str(day)]={'supported_origins':len(d),'scores':{n:score(u,p,d) for n,p in corrected.items()} if len(d) else {}}
  results[key]={'support':selection[key],'scores':scores,'raw_scores':{n:score(u,p,ids) for n,p in raw.items()},
   'chosen_baseline':chosen,'gain_vs_equally_corrected_baseline':gain,'daily':daily,
   'gate':'RETROSPECTIVE_TRANSFER_PASS' if gain>=.05 and .85<=cov<=.95 else 'NO_GO'}
 save(out/'results.json',{'comparisons':results,'overall_transfer_pass':len(results)==12 and all(v['gate']=='RETROSPECTIVE_TRANSFER_PASS' for v in results.values()),
  'new_models_trained':0,'operational_adoption':False,'historical_data_previously_explored':True})
 check(s);print(json.dumps({k:{n:v[n] for n in ('gate','gain_vs_equally_corrected_baseline') if n in v} for k,v in results.items()},indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','run']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args();{'prepare':prepare,'run':run}[a.mode](a.stage)
