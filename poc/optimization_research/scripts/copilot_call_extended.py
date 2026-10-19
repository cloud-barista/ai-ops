"""Expanded source-period call-count experiment; previously exposed evaluation."""
import argparse,datetime,gzip,json,tarfile
from pathlib import Path
import orjson,numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from train_evaluate_delay_capacity import FEATURES,PARAMS,sha,pinball
from train_copilot_call_demand import design as old_design
from copilot_demand_transfer import time_bin
from delay_capacity_policy import peak_targets
ROOT=Path('/home/researcher/ai-mcmp/research');N=1440


def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False)


def frame(y):
 # Reuse tested lag construction but set actual June3 UTC clock/calendar.
 f=old_design(y);t=pd.date_range('2026-06-03',periods=len(y),freq='5min',tz='UTC')
 clock=t.hour.to_numpy()*12+t.minute.to_numpy()//5
 f['clock_sin']=np.sin(2*np.pi*clock/288);f['clock_cos']=np.cos(2*np.pi*clock/288)
 f['weekend']=(t.dayofweek.to_numpy()>=5).astype(float)
 return f


def prepare(s):
 o=s/'artifacts/copilot_call_demand_v2';o.mkdir(parents=True,exist_ok=False)
 metadata=ROOT/'data/external/ghcp_coding_agent_2026_days05_07/release_metadata.json'
 assets=json.loads(metadata.read_text())['assets'];names=[f'date.2026-06-{d:02}.tar.gz' for d in range(3,8)]
 assets=[next(a for a in assets if a['name']==n) for n in names]
 files=[Path(__file__),s/'tests/test_copilot_call_extended.py',s/'reports/copilot_call_extended_protocol_v2.json']
 helpers=['train_evaluate_delay_capacity.py','train_copilot_call_demand.py','copilot_demand_transfer.py','delay_capacity_policy.py','demand_capacity_policy.py']
 models={p.name:sha(p) for p in (ROOT/'artifacts/copilot_call_demand_v1').glob('*.joblib')}
 save(o/'FREEZE.json',{'assets':[{k:a[k] for k in ('name','size','digest','browser_download_url')} for a in assets],
  'code':{p.name:sha(p) for p in files},'helpers':{n:sha(ROOT/'scripts'/n) for n in helpers},
  'v1_models':models,'release_metadata_sha256':sha(metadata),'frozen_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'protocol':json.loads(files[-1].read_text()),'evaluation_previously_seen':True,'frozen_before_added_data_download_and_fit':True})
 print('FROZEN new raw bytes '+str(sum(a['size'] for a in assets[:2])))


def paths(s,f):
 result=[]
 for a in f['assets']:
  p=(s/'data'/a['name'] if a['name'].endswith(('03.tar.gz','04.tar.gz')) else ROOT/'data/external/ghcp_coding_agent_2026_days05_07'/a['name'])
  assert p.stat().st_size==a['size'] and sha(p)==a['digest'].split(':')[1]
  result.append(p)
 return result


def aggregate(paths,independent=False):
 counts=np.zeros(N,dtype=np.int64);sessions=set();calls=outside=0
 for p in paths:
  part=p.name[5:-7];pc=ps=0
  with tarfile.open(p,'r|gz') as archive:
   for entry in archive:
    if not entry.isfile() or not entry.name.endswith('.jsonl.gz'):continue
    stamps=[]
    with archive.extractfile(entry) as raw,gzip.GzipFile(fileobj=raw) as stream:
     for line in stream:
      row=(json.loads(line) if independent else orjson.loads(line));assert row['date']==part
      assert row['session_id'] not in sessions,'repeated session';sessions.add(row['session_id']);ps+=1
      for turn in row['turns']:
       for c in turn['llm_calls']:
        pc+=1
        if independent:stamps.append(c['timestamp'])
        else:
         i,naive=time_bin(c['timestamp']);assert not naive;i+=576
         if 0<=i<N:counts[i]+=1
         else:outside+=1
    if independent and stamps:
     start=pd.Timestamp('2026-06-03T00:00:00Z').value
     bins=(pd.to_datetime(stamps,format='mixed',utc=True).asi8-start)//(300*10**9)
     good=(bins>=0)&(bins<N);outside+=int((~good).sum());np.add.at(counts,bins[good],1)
  calls+=pc;print(json.dumps({'partition':part,'sessions':ps,'calls':pc,'independent':independent}),flush=True)
 assert counts.sum()==calls-outside
 return counts,{'sessions':len(sessions),'calls':calls,'outside_window':outside,'in_window':int(counts.sum())}


def baselines(y,m,f,h):
 tr=np.arange(12,864-h);target=peak_targets(y,h);ratio=target[tr]/m[tr];order=np.argsort(ratio,kind='stable');w=m[tr][order]
 q=ratio[order[min(int(np.searchsorted(np.cumsum(w),.9*w.sum())),len(w)-1)]]
 return {'train_weighted_peak_q90':q*m,'past_hour_max':f.hour_max.to_numpy(float),'past_hour_q90':f.hour_q90.to_numpy(float),'past_hour_mean':m.copy()}


def train(s):
 o=s/'artifacts/copilot_call_demand_v2';freeze=json.loads((o/'FREEZE.json').read_text())
 check(s,freeze)
 y,a=aggregate(paths(s,freeze));save(o/'source_audit.json',a)
 pd.DataFrame({'recorded_calls':y}).to_parquet(o/'counts.parquet',index=False)
 f=frame(y);m=f.scale.to_numpy(float);X=f[list(FEATURES)].to_numpy(float);packs={};selection={}
 for h in (1,2,6):
  target=peak_targets(y,h);tr=np.arange(12,864-h);val=np.arange(864,1152-h)
  assert (m[tr]>=1).all() and (m[val]>=1).all()
  model=HistGradientBoostingRegressor(**PARAMS);model.fit(X[tr],target[tr]/m[tr],sample_weight=m[tr]/m[tr].mean())
  p=o/f'copilot_delay{h}_extended_q90.joblib'
  joblib.dump({'model':model,'delay':h,'features':FEATURES,'training_origins':tr,'params':PARAMS},p,compress=3)
  old=joblib.load(ROOT/'artifacts/copilot_call_demand_v1'/f'copilot_delay{h}_call_peak_q90.joblib')
  base=baselines(y,m,f,h);chosen=min(base,key=lambda k:float(pinball(target[val],base[k][val]).mean()))
  packs[h]=(target,np.maximum(0,model.predict(X))*m,np.maximum(0,old['model'].predict(X))*m,base,chosen)
  selection[str(h)]={'model_sha256':sha(p),'baseline':chosen,'training_rows':len(tr),'validation_rows':len(val)}
 save(o/'SELECTION.json',{'validation_only':selection,'saved_before_test_scores':True})
 results={};cols={'y':y,'scale':m}
 for h,(target,new,old,base,chosen) in packs.items():
  ids=np.arange(1152,1440-h);lp=float(pinball(target[ids],new[ids]).mean());lb=float(pinball(target[ids],base[chosen][ids]).mean());lo=float(pinball(target[ids],old[ids]).mean())
  cov=float(np.mean(target[ids]<=new[ids]));gain=1-lp/lb;passed=gain>=.05 and .85<=cov<=.95
  results[str(h)]={'test_rows':len(ids),'pinball':lp,'baseline_pinball':lb,'frozen_v1_pinball_on_same_new_series':lo,
   'relative_baseline_loss_reduction':gain,'relative_v1_loss_reduction':1-lp/lo,'coverage':cov,'baseline':chosen,
   'gate':'EXPLORATORY_PASS' if passed else 'NO_GO'}
  cols[f'd{h}_target']=target;cols[f'd{h}_new']=new;cols[f'd{h}_old']=old;cols[f'd{h}_baseline']=base[chosen]
 pd.DataFrame(cols).to_parquet(o/'forecasts.parquet',index=False)
 save(o/'results.json',{'methods':results,'new_models':3,'test_previously_seen':True,'operational_adoption':False,
  'call_count_label_only':True,'added_prior_sessions_can_change_later_bins':True,'isolated_training_duration_effect_claimed':False})
 check(s,freeze);print(json.dumps(results))


def check(s,freeze):
 for n,d in freeze['code'].items():
  p=next(s.glob('*/'+n));assert sha(p)==d,n
 for n,d in freeze['helpers'].items():assert sha(ROOT/'scripts'/n)==d,n
 for n,d in freeze['v1_models'].items():assert sha(ROOT/'artifacts/copilot_call_demand_v1'/n)==d,n


def verify(s):
 o=s/'artifacts/copilot_call_demand_v2';freeze=json.loads((o/'FREEZE.json').read_text());check(s,freeze)
 y,a=aggregate(paths(s,freeze),True)
 np.testing.assert_array_equal(y,pd.read_parquet(o/'counts.parquet').recorded_calls)
 assert a==json.loads((o/'source_audit.json').read_text())
 f=frame(y);m=f.scale.to_numpy(float);X=f[list(FEATURES)].to_numpy(float);saved=pd.read_parquet(o/'forecasts.parquet')
 r=json.loads((o/'results.json').read_text());selection=json.loads((o/'SELECTION.json').read_text())['validation_only']
 for h in (1,2,6):
  tr=np.arange(12,864-h);val=np.arange(864,1152-h);ids=np.arange(1152,1440-h)
  target=np.full(N,np.nan)
  for i in range(N-h):target[i]=max(y[i:i+h+1])
  assert tr[-1]+h+1==864 and val[-1]+h+1==1152
  p=o/f'copilot_delay{h}_extended_q90.joblib';assert sha(p)==selection[str(h)]['model_sha256'];bundle=joblib.load(p)
  fresh=HistGradientBoostingRegressor(**PARAMS);fresh.fit(X[tr],target[tr]/m[tr],sample_weight=m[tr]/m[tr].mean())
  np.testing.assert_array_equal(bundle['model'].predict(X),fresh.predict(X))
  new=np.maximum(0,fresh.predict(X))*m;np.testing.assert_allclose(new,saved[f'd{h}_new'],rtol=0,atol=0,equal_nan=True)
  base=baselines(y,m,f,h);chosen=min(base,key=lambda k:float(pinball(target[val],base[k][val]).mean()))
  assert chosen==selection[str(h)]['baseline']
  e=target[ids]-new[ids];lp=float(np.where(e>=0,.9*e,-.1*e).mean());lb=float(pinball(target[ids],base[chosen][ids]).mean())
  old=joblib.load(ROOT/'artifacts/copilot_call_demand_v1'/f'copilot_delay{h}_call_peak_q90.joblib')
  pp=np.maximum(0,old['model'].predict(X))*m;np.testing.assert_allclose(pp,saved[f'd{h}_old'],rtol=0,atol=0,equal_nan=True)
  lo=float(pinball(target[ids],pp[ids]).mean());cov=float(np.mean(target[ids]<=new[ids]));gain=1-lp/lb
  for k,v in {'pinball':lp,'baseline_pinball':lb,'frozen_v1_pinball_on_same_new_series':lo,'coverage':cov,'relative_baseline_loss_reduction':gain,'relative_v1_loss_reduction':1-lp/lo}.items():np.testing.assert_allclose(v,r['methods'][str(h)][k],rtol=1e-14)
  assert r['methods'][str(h)]['gate']==('EXPLORATORY_PASS' if gain>=.05 and .85<=cov<=.95 else 'NO_GO')
 mutant=y.copy();mutant[1152:]=10**9
 np.testing.assert_allclose(frame(y)[list(FEATURES)].iloc[:1153],frame(mutant)[list(FEATURES)].iloc[:1153],rtol=0,atol=0,equal_nan=True)
 save(o/'verification.json',{'status':'VERIFIED','independent_raw_count_bins_exact':N,'source_statistics_equal':True,
  'models_refit_exact':3,'validation_selection_and_test_metrics_replayed':True,'future_mutation_passed':True,'source_and_model_hashes_unchanged':True})
 print('VERIFIED')


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','train','verify']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
 {'prepare':prepare,'train':train,'verify':verify}[a.mode](a.stage)
