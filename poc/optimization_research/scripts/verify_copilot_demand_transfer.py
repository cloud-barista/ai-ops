"""Independent raw binning, forecast replay, and calibration-only score audit."""
import argparse
import gzip
import json
import tarfile
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from copilot_demand_transfer import features,verify_dependencies
from train_evaluate_delay_capacity import sha,FEATURES


def aggregate_independently(paths):
    totals=np.zeros(864,dtype=np.int64);counts=totals.copy();calls=0;invalid=0;outside=0
    start=pd.Timestamp('2026-06-05T00:00:00Z').value
    for path in paths:
        with tarfile.open(path,'r|gz') as archive:
            for entry in archive:
                if not entry.isfile() or not entry.name.endswith('.jsonl.gz'):continue
                timestamps=[];prompts=[]
                with archive.extractfile(entry) as raw:
                    with gzip.GzipFile(fileobj=raw) as stream:
                        for line in stream:
                            row=json.loads(line)
                            for turn in row['turns']:
                                for call in turn['llm_calls']:
                                    calls+=1;timestamps.append(call['timestamp']);prompts.append(call.get('tokens',{}).get('prompt'))
                if not timestamps:continue
                # Pandas nanosecond parsing is independent of the scorer's datetime parser.
                stamps=pd.to_datetime(timestamps,format='mixed',utc=True,errors='raise')
                bins=((stamps.asi8-start)//(300*10**9)).astype(np.int64)
                valid_time=(bins>=0)&(bins<864);outside+=int((~valid_time).sum())
                valid_prompt=np.array([isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in prompts])
                invalid+=int((valid_time&~valid_prompt).sum())
                use=valid_time&valid_prompt
                amounts=np.array([v if ok else 0 for v,ok in zip(prompts,valid_prompt)],dtype=np.int64)
                # Integer scatter avoids the scorer's per-call update and float bincount sums.
                np.add.at(totals,bins[use],amounts[use]);np.add.at(counts,bins[use],1)
        print('Independently aggregated '+path.name,flush=True)
    return totals,counts,{'llm_calls':calls,'invalid_prompt_calls':invalid,'out_of_window_calls':outside}


def corrected_independently(y,p,scale,h):
    target=np.full(len(y),np.nan)
    for i in range(len(y)-h):target[i]=max(y[i:i+h+1])
    q=p.copy()
    for t in range(12,len(y)):
        ids=np.arange(max(12,t-h-288),t-h)
        ids=ids[(np.isfinite(target[ids])&np.isfinite(p[ids])&np.isfinite(scale[ids])&(scale[ids]>=1000))]
        if len(ids)>=96 and np.isfinite(p[t]) and scale[t]>=1000:
            residual=np.sort((target[ids]-p[ids])/scale[ids])
            # Type-7 sample quantile (NumPy default), implemented through interpolation.
            loc=.9*(len(residual)-1);lo=int(np.floor(loc));hi=int(np.ceil(loc))
            quantile=residual[lo]+(loc-lo)*(residual[hi]-residual[lo])
            q[t]+=scale[t]*max(0.,quantile)
    return target,q


def loss(y,p):
    e=y-p
    return np.where(e>=0,.9*e,-.1*e)


def verify(root,stage):
    out=stage/'artifacts/copilot_transfer_v1_1'
    freeze=json.loads((out/'PRE_SCORE_FREEZE.json').read_text())
    verify_dependencies(root,stage,freeze)
    if sha(stage/'scripts/copilot_demand_transfer.py')!=freeze['code_hash']:raise ValueError('score code changed')
    for name,digest in json.loads((out/'manifest.json').read_text()).items():
        if sha(out/name)!=digest:raise ValueError('artifact changed '+name)
    paths=[stage/'data'/a['name'] for a in freeze['assets']]
    for path,a in zip(paths,freeze['assets']):
        if sha(path)!=a['digest'].split(':')[1] or path.stat().st_size!=a['size']:raise ValueError('raw changed')
    y,c,audit=aggregate_independently(paths)
    original=pd.read_parquet(out/'five_minute_counts.parquet')
    np.testing.assert_array_equal(y,original.prompt_tokens)
    np.testing.assert_array_equal(c,original.llm_calls)
    primary=json.loads((out/'data_audit.json').read_text())
    for k,v in audit.items():
        if primary[k]!=v:raise ValueError('independent count differs '+k)
    if primary['exact_repeated_call_records']!=0:raise ValueError('duplicates cannot pass independent audit')
    saved=pd.read_parquet(out/'predictions.parquet');selection=json.loads((out/'BASELINE_SELECTION.json').read_text())
    results=json.loads((out/'results.json').read_text());frame=features(y);m=frame.scale.to_numpy(float)
    np.testing.assert_array_equal(saved.y,y);np.testing.assert_allclose(saved.scale,m,rtol=0,atol=0)
    models=[]
    for h in (1,2,6):
        target=np.full(864,np.nan)
        for t in range(864-h):target[t]=np.max(y[t:t+h+1])
        candidates={'past_hour_max':frame.hour_max.to_numpy(float),'past_hour_q90':frame.hour_q90.to_numpy(float),'past_hour_mean':m.copy()}
        for source in ('coding','lmm'):
            sourceframe=pd.read_parquet(root/'artifacts/delay_capacity_v2'/f'{source}_delay1_forecasts.parquet')
            sy=sourceframe.y.to_numpy(float);sm=sourceframe.scale.to_numpy(float)
            ids=np.arange(12,864-h)
            ratios=np.array([max(sy[t:t+h+1])/sm[t] for t in ids])
            order=np.argsort(ratios,kind='stable');cum=np.cumsum(sm[ids][order]);ix=np.where(cum>=.9*cum[-1])[0][0]
            candidates[source+'_source_peak_q90']=ratios[order[ix]]*m
        cal=np.arange(12,288-h);cal=cal[m[cal]>=1000]
        chosen=min(candidates,key=lambda k:loss(target[cal],candidates[k][cal]).mean())
        if chosen!=selection[str(h)]['baseline']:raise ValueError('calibration selection differs')
        _,reference=corrected_independently(y,candidates[chosen],m,h)
        np.testing.assert_allclose(reference,saved[f'baseline_d{h}_feedback'],rtol=1e-14,atol=1e-6)
        for source in ('coding','lmm'):
            key=f'{source}_d{h}';bundle=joblib.load(root/'artifacts/delay_capacity_v2'/f'{source}_delay{h}_peak_q90.joblib')
            raw=np.maximum(0,bundle['model'].predict(frame[list(FEATURES)].to_numpy(float)))*m
            np.testing.assert_allclose(raw,saved[key+'_raw'],rtol=0,atol=0)
            _,p=corrected_independently(y,raw,m,h)
            np.testing.assert_allclose(p,saved[key+'_feedback'],rtol=1e-14,atol=1e-6)
            days=[]
            for day in (1,2):
                ids=np.arange(day*288,min((day+1)*288,864-h));ids=ids[m[ids]>=1000]
                record={'day':day,'bins':len(ids),'pinball':float(loss(target[ids],p[ids]).mean()),
                    'baseline_pinball':float(loss(target[ids],reference[ids]).mean()),'raw_pinball':float(loss(target[ids],raw[ids]).mean()),
                    'coverage':float(np.mean(target[ids]<=p[ids])),'baseline_coverage':float(np.mean(target[ids]<=reference[ids]))}
                for k,v in record.items():np.testing.assert_allclose(v,results['methods'][key]['days'][day-1][k],rtol=1e-13,atol=1e-6)
                days.append(record)
            gain=1-np.mean([d['pinball'] for d in days])/np.mean([d['baseline_pinball'] for d in days])
            passed=gain>=.05 and all(d['pinball']<d['baseline_pinball'] and .85<=d['coverage']<=.95 for d in days)
            if results['methods'][key]['gate']!=('EXTERNAL_SAMPLE_PASS' if passed else 'NO_GO'):raise ValueError('gate differs')
            np.testing.assert_allclose(gain,results['methods'][key]['relative_pinball_reduction'],rtol=1e-13,atol=1e-12)
            models.append(key)
    mutated=y.copy();mutated[288:]=10**12
    np.testing.assert_allclose(features(y)[list(FEATURES)].iloc[:289],features(mutated)[list(FEATURES)].iloc[:289],rtol=0,atol=0,equal_nan=True)
    for h in (1,2,6):
        p=saved[f'coding_d{h}_raw'].to_numpy(float)
        _,a=corrected_independently(y,p,m,h);_,b=corrected_independently(mutated,p,m,h)
        np.testing.assert_array_equal(a[:289],b[:289])
    record={'status':'VERIFIED','raw_archive_hashes_checked':True,'independent_integer_aggregation':True,
        'all_864_token_and_call_bins_exact':True,'six_fixed_models_replayed':models,
        'three_calibration_only_baselines_reselected':True,'independent_mature_feedback_and_daily_metrics':True,
        'evaluation_future_mutation_does_not_change_calibration_or_first_evaluation_decision':True,
        'source_model_and_helper_hashes_unchanged':True,'verifier_sha256':sha(Path(__file__))}
    with (out/'independent_audit.json').open('x') as f:json.dump(record,f,indent=2)
    print(json.dumps(record))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--research-root',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();verify(a.research_root,a.stage)
