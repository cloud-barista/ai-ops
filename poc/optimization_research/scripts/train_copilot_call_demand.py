"""Three service-specific peak call-arrival q90 models; unchanged token NO_GO."""
import argparse
import datetime as dt
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from train_evaluate_delay_capacity import FEATURES,PARAMS,sha,pinball
from delay_capacity_policy import peak_targets


def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2,allow_nan=False)


def design(y):
    a=pd.Series(np.asarray(y,float));past=a.shift(1);m=past.rolling(12,min_periods=12).mean()
    ratio=a/m.where(m>=1);stamps=pd.date_range('2026-06-05',periods=len(y),freq='5min',tz='UTC')
    clock=stamps.hour.to_numpy()*12+stamps.minute.to_numpy()//5
    hourq=past.rolling(12,min_periods=12).quantile(.9);hourmax=past.rolling(12,min_periods=12).max()
    old=a.shift(7).rolling(6,min_periods=6).mean()
    frame=pd.DataFrame({'scale':m,'lag1_over_scale':past/m,'lag2_over_scale':a.shift(2)/m,
        'lag3_over_scale':a.shift(3)/m,'lag12_over_scale':a.shift(12)/m,'previous_day_ratio':ratio.shift(288),
        'past_hour_max_over_scale':hourmax/m,'past_hour_q90_over_scale':hourq/m,
        'recent_six_over_previous_six':past.rolling(6,min_periods=6).mean()/old.where(old>0),
        'clock_sin':np.sin(2*np.pi*clock/288),'clock_cos':np.cos(2*np.pi*clock/288),
        'weekend':(stamps.dayofweek.to_numpy()>=5).astype(float),'hour_max':hourmax,'hour_q90':hourq})
    return frame.replace([np.inf,-np.inf],np.nan)


def candidates(y,m,frame,h):
    train=np.arange(12,288-h);target=peak_targets(y,h)
    ratios=target[train]/m[train];order=np.argsort(ratios,kind='stable');w=m[train][order]
    ix=min(int(np.searchsorted(np.cumsum(w),.9*w.sum())),len(w)-1)
    return {'train_weighted_relative_peak_q90':ratios[order[ix]]*m,
        'past_hour_max':frame.hour_max.to_numpy(float),'past_hour_q90':frame.hour_q90.to_numpy(float),
        'past_hour_mean':m.copy()}


def prepare(stage):
    out=stage/'artifacts/copilot_call_demand_v1';out.mkdir(exist_ok=False)
    protocol=stage/'reports/copilot_call_demand_protocol_v1.json'
    files=[Path(__file__),stage/'scripts/verify_copilot_call_demand.py',stage/'tests/test_copilot_call_demand.py',protocol]
    helpers=Path('/home/researcher/ai-mcmp/research/scripts')
    save(out/'CODE_FREEZE.json',{'code':{p.name:sha(p) for p in files},
        'helpers':{n:sha(helpers/n) for n in ('train_evaluate_delay_capacity.py','delay_capacity_policy.py','demand_capacity_policy.py')},
        'protocol':json.loads(protocol.read_text()),'frozen_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_audit_freeze_sha256':sha(stage/'artifacts/copilot_transfer_v1_1/AUDIT_VERIFIER_FREEZE.json'),
        'frozen_before_new_fit_or_any_call_demand_forecast_score':True,
        'raw_structure_and_some_bad_token_aggregates_previously_inspected':True})


def train(stage):
    source=stage/'artifacts/copilot_transfer_v1_1';out=stage/'artifacts/copilot_call_demand_v1'
    freeze=json.loads((out/'CODE_FREEZE.json').read_text())
    for folder,names in (('scripts',('train_copilot_call_demand.py','verify_copilot_call_demand.py')),('tests',('test_copilot_call_demand.py',)),('reports',('copilot_call_demand_protocol_v1.json',))):
        for name in names:assert sha(stage/folder/name)==freeze['code'][name]
    for name,digest in freeze['helpers'].items():assert sha(Path('/home/researcher/ai-mcmp/research/scripts')/name)==digest
    assert sklearn.__version__=='1.6.1' and np.__version__=='2.0.2'
    audit=json.loads((source/'independent_source_audit.json').read_text())
    assert audit['status']=='VERIFIED_SOURCE_LABEL_NO_GO' and audit['audit']['exact_repeated_call_records']==0
    inputfile=source/'complete_call_counts_and_known_tokens.parquet'
    save(out/'INPUT_FREEZE.json',{'input_sha256':sha(inputfile),'source_audit_sha256':sha(source/'independent_source_audit.json')})
    y=pd.read_parquet(inputfile).recorded_llm_calls.to_numpy(float)
    assert len(y)==864 and np.isfinite(y).all() and (y>=0).all()
    frame=design(y);m=frame.scale.to_numpy(float);X=frame[list(FEATURES)].to_numpy(float)
    packs={};selection={};hashes={}
    for h in (1,2,6):
        target=peak_targets(y,h);train=np.arange(12,288-h);val=np.arange(288,576-h)
        assert (m[train]>=1).all() and (m[val]>=1).all() and train[-1]+h+1==288
        model=HistGradientBoostingRegressor(**PARAMS)
        model.fit(X[train],target[train]/m[train],sample_weight=m[train]/m[train].mean())
        path=out/f'copilot_delay{h}_call_peak_q90.joblib'
        joblib.dump({'model':model,'features':FEATURES,'delay':h,'unit':'recorded LLM calls per 5min',
            'train_origins':train,'parameters':PARAMS},path,compress=3);hashes[path.name]=sha(path)
        p=np.maximum(0,model.predict(X))*m
        base=candidates(y,m,frame,h)
        chosen=min(base,key=lambda k:float(pinball(target[val],base[k][val]).mean()))
        selection[str(h)]={'baseline':chosen,'train_rows':len(train),'validation_rows':len(val)}
        packs[h]=(target,p,base,chosen)
    save(out/'SELECTION_FREEZE.json',{'model_hashes':hashes,'validation_only_baselines':selection,'saved_before_test_scores':True})
    results={};columns={'observed_calls':y,'scale':m}
    for h,(target,p,base,chosen) in packs.items():
        test=np.arange(576,864-h);assert (m[test]>=1).all()
        lp=float(pinball(target[test],p[test]).mean());lb=float(pinball(target[test],base[chosen][test]).mean())
        cov=float(np.mean(target[test]<=p[test]));gain=1-lp/lb
        passed=gain>=.05 and .85<=cov<=.95
        results[str(h)]={'gate':'EXPLORATORY_CALL_FORECAST_PASS' if passed else 'NO_GO','prepare_minutes':h*5,
            'target_window_minutes':(h+1)*5,'test_rows':len(test),'pinball':lp,'baseline_pinball':lb,
            'relative_loss_reduction':gain,'coverage':cov,'baseline_coverage':float(np.mean(target[test]<=base[chosen][test])),
            'baseline':chosen,'all_baseline_test_pinball':{k:float(pinball(target[test],v[test]).mean()) for k,v in base.items()}}
        columns[f'd{h}_target']=target;columns[f'd{h}_model']=p
        for k,v in base.items():columns[f'd{h}_{k}']=v
    pd.DataFrame(columns).to_parquet(out/'forecasts.parquet',index=False)
    save(out/'results.json',{'status':'EXPLORATORY_SERVICE_SPECIFIC_CALL_FORECAST','methods':results,
        'new_models_trained':3,'training_day':'2026-06-05','validation_day':'2026-06-06','test_day':'2026-06-07',
        'label':'all recorded call occurrences, including null prompt and cancelled/failed calls',
        'target_is_not_prompt_tokens':True,'old_token_transfer_quality_no_go_preserved':True,
        'actual_gpu_vm_or_action_effect_measured':False,'operational_adoption':False})
    print(json.dumps(results))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('mode',choices=['prepare','train']);a.add_argument('--stage',type=Path,required=True)
    p=a.parse_args();(prepare if p.mode=='prepare' else train)(p.stage)
