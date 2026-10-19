"""Independent replay with explicit eligible IDs and sorted interpolation."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def percentile(values):
    values=sorted(values);pos=.9*(len(values)-1);j=math.floor(pos)
    return values[j]+(pos-j)*(values[min(j+1,len(values)-1)]-values[j])


def reference(target,p,m,h,lag):
    out=p.copy();counts=np.zeros(len(p),dtype=int);d=np.zeros(len(p));last=np.full(len(p),-1,dtype=int)
    for t in range(864,len(p)):
        ids=[i for i in range(max(864,t-h-lag-288),t-h-lag)
             if math.isfinite(target[i]) and target[i]>=0 and math.isfinite(p[i]) and p[i]>=0
             and math.isfinite(m[i]) and m[i]>=1]
        if not math.isfinite(p[t]) or p[t]<0 or not math.isfinite(m[t]) or m[t]<1:continue
        assert all(i+h+1+lag<=t and i>=864 for i in ids)
        counts[t]=len(ids)
        if ids:last[t]=ids[-1]+h+1+lag
        if len(ids)>=96:
            d[t]=max(0.,percentile([(target[i]-p[i])/m[i] for i in ids]))
            out[t]+=m[t]*d[t]
    return out,counts,d,last


def metric(target,p,ids):
    errors=[target[i]-p[i] for i in ids]
    return {'rows':len(ids),'pinball':float(sum(.9*e if e>=0 else -.1*e for e in errors)/len(ids)),
            'coverage':float(sum(target[i]<=p[i] for i in ids)/len(ids))}


def main(stage):
    root=Path('/home/researcher/ai-mcmp/research');parent=root/'artifacts/copilot_call_demand_v2'
    out=stage/'artifacts/copilot_call_feedback_v1'
    freeze=json.loads((out/'FREEZE.json').read_text())
    for n,d in freeze['code'].items():assert sha(stage/n)==d,n
    for n,d in freeze['inputs'].items():assert sha(Path(n))==d,n
    y=pd.read_parquet(parent/'counts.parquet').recorded_calls.to_numpy(float)
    f=pd.read_parquet(parent/'forecasts.parquet');m=f.scale.to_numpy(float)
    np.testing.assert_array_equal(y,f.y)
    for i in range(12,len(y)):np.testing.assert_allclose(m[i],sum(y[i-12:i])/12,rtol=1e-12)
    replay=pd.read_parquet(out/'replay.parquet')
    selection=json.loads((out/'SELECTION.json').read_text())['validation_only']
    scores=json.loads((out/'results.json').read_text())['comparisons']
    comparisons=rows=0
    for h in (1,2,6):
        target=np.array([max(y[i:i+h+1]) if i+h<len(y) else np.nan for i in range(len(y))])
        np.testing.assert_allclose(target,f[f'd{h}_target'],rtol=0,atol=0,equal_nan=True)
        ratio_weights=sorted((target[i]/m[i],m[i]) for i in range(12,864-h))
        cut=.9*sum(w for q,w in ratio_weights);total=0.
        for q,w in ratio_weights:
            total+=w
            if total>=cut:break
        raw={'hgb':f[f'd{h}_new'].to_numpy(float),'train_weighted_peak_q90':q*m}
        for name,func in [('past_hour_max',max),('past_hour_q90',percentile),('past_hour_mean',lambda v:sum(v)/len(v))]:
            raw[name]=np.array([func(list(y[i-12:i])) if i>=12 else np.nan for i in range(len(y))])
        for lag in (0,1,6):
            preds={}
            for method,p in raw.items():
                ref,n,d,last=reference(target,p,m,h,lag);preds[method]=ref
                saved=replay[(replay.delay_bins==h)&(replay.delivery_lag_bins==lag)&(replay.method==method)].sort_values('origin')
                np.testing.assert_array_equal(saved.origin,np.arange(len(y)))
                np.testing.assert_allclose(saved.raw,p,rtol=1e-12,atol=1e-12,equal_nan=True)
                np.testing.assert_allclose(saved.corrected,ref,rtol=1e-12,atol=1e-12,equal_nan=True)
                np.testing.assert_array_equal(saved.feedback_count,n)
                np.testing.assert_allclose(saved.normalized_delta,d,rtol=1e-12,atol=1e-12)
                np.testing.assert_array_equal(saved.last_feedback_available_bin,last)
                comparisons+=1;rows+=len(saved)
            key=f'{h}:{lag}';val=list(range(864,1152-h));ids=list(range(1152,1440-h))
            chosen=min(list(raw)[1:],key=lambda name:metric(target,preds[name],val)['pinball'])
            assert chosen==selection[key]['baseline']==scores[key]['chosen_baseline']
            for name in raw:
                for split,newpred,stored in [('val',preds[name],selection[key]['validation_scores'][name]),
                    ('test',preds[name],scores[key]['scores'][name]),('raw',raw[name],scores[key]['raw_scores'][name])]:
                    got=metric(target,newpred,val if split=='val' else ids)
                    for k in got:np.testing.assert_allclose(got[k],stored[k],rtol=1e-12,atol=1e-12)
            a=metric(target,preds['hgb'],ids);b=metric(target,preds[chosen],ids)
            gain=1-a['pinball']/b['pinball']
            np.testing.assert_allclose(gain,scores[key]['model_gain_vs_corrected_baseline'],rtol=1e-12,atol=1e-12)
            assert scores[key]['gate']==('EXPLORATORY_PASS' if gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO')
            mutant=y.copy();mutant[1152:]=1e9
            changed=np.array([max(mutant[i:i+h+1]) if i+h<len(y) else np.nan for i in range(len(y))])
            perturbed=reference(changed,raw['hgb'],m,h,lag)[0]
            np.testing.assert_allclose(perturbed[:1153],preds['hgb'][:1153],rtol=0,atol=0,equal_nan=True)
    assert len(replay)==rows==64800 and comparisons==45
    for n,d in freeze['inputs'].items():assert sha(Path(n))==d,n
    with (out/'verification.json').open('x') as stream:
        json.dump({'status':'VERIFIED','independent_replays':comparisons,'forecast_rows':rows,
                   'manual_target_and_quantile_replay':True,'selection_metrics_and_gates_checked':True,
                   'future_mutation_all_delays_and_lags':True,'inputs_unchanged':True},stream,indent=2)
    print('VERIFIED 45 independent replays / 64800 rows')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--stage',required=True,type=Path);args=ap.parse_args();main(args.stage)
