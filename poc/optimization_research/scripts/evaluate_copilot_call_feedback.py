"""Frozen, retrospective evaluation of completed-label call-demand correction."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from copilot_call_feedback import correct_completed

ROOT = Path('/home/researcher/ai-mcmp/research')
PARENT = ROOT / 'artifacts/copilot_call_demand_v2'
METHODS = ('hgb', 'train_weighted_peak_q90', 'past_hour_max', 'past_hour_q90', 'past_hour_mean')


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def save(p, v):
    with p.open('x', encoding='utf8') as f:
        json.dump(v, f, indent=2, allow_nan=False, ensure_ascii=False); f.write('\n')


def prepare(stage):
    out = stage/'artifacts/copilot_call_feedback_v1'
    out.mkdir(parents=True, exist_ok=False)
    assert json.loads((PARENT/'verification.json').read_text())['status'] == 'VERIFIED'
    manifest = json.loads((PARENT/'release_manifest.json').read_text())
    for n,d in manifest.items(): assert sha(PARENT/n)==d,n
    sources = [stage/'scripts/copilot_call_feedback.py', Path(__file__),
               stage/'tests/test_copilot_call_feedback.py', stage/'scripts/verify_copilot_call_feedback.py',
               stage/'reports/copilot_call_feedback_protocol_v1.json']
    inputs = [PARENT/n for n in ('forecasts.parquet','counts.parquet','results.json','verification.json','SELECTION.json')]
    inputs += list(PARENT.glob('*.joblib'))
    save(out/'FREEZE.json', {'frozen_at':datetime.now(timezone.utc).isoformat(),
         'code':{str(p.relative_to(stage)):sha(p) for p in sources},
         'inputs':{str(p):sha(p) for p in inputs},
         'protocol':json.loads(sources[-1].read_text()), 'python':sys.version,
         'numpy':np.__version__, 'pandas':pd.__version__, 'test_previously_seen':True})


def check(stage):
    freeze=json.loads((stage/'artifacts/copilot_call_feedback_v1/FREEZE.json').read_text())
    for n,d in freeze['code'].items(): assert sha(stage/n)==d,n
    for n,d in freeze['inputs'].items(): assert sha(Path(n))==d,n


def bases(y,m,h):
    target=np.r_[np.max(np.lib.stride_tricks.sliding_window_view(y,h+1),axis=1),np.full(h,np.nan)]
    tr=np.arange(12,864-h)
    ratio=target[tr]/m[tr]; order=np.argsort(ratio,kind='stable'); weights=m[tr][order]
    idx=min(int(np.searchsorted(np.cumsum(weights),.9*weights.sum())),len(order)-1)
    q=ratio[order[idx]]
    prev=pd.Series(y).shift(1).rolling(12,min_periods=12)
    return target,{'train_weighted_peak_q90':q*m,'past_hour_max':prev.max().to_numpy(),
                  'past_hour_q90':prev.quantile(.9).to_numpy(),'past_hour_mean':prev.mean().to_numpy()}


def score(target,p,ids):
    error=target[ids]-p[ids]
    assert np.isfinite(error).all()
    return {'rows':len(ids),'pinball':float(np.maximum(.9*error,-.1*error).mean()),
            'coverage':float(np.mean(target[ids]<=p[ids]))}


def run(stage):
    check(stage)
    out=stage/'artifacts/copilot_call_feedback_v1'
    f=pd.read_parquet(PARENT/'forecasts.parquet')
    y=f.y.to_numpy(); m=f.scale.to_numpy(); rows=[]; packs={}; selections={}
    for h in (1,2,6):
        target,base=bases(y,m,h)
        np.testing.assert_allclose(target,f[f'd{h}_target'],rtol=0,atol=0,equal_nan=True)
        raw={'hgb':f[f'd{h}_new'].to_numpy(),**base}
        val=np.arange(864,1152-h)
        for lag in (0,1,6):
            preds={}
            for method,p in raw.items():
                corrected,n,delta,available=correct_completed(target,p,m,h,delivery_lag=lag)
                preds[method]=corrected
                rows.append(pd.DataFrame({'origin':np.arange(len(y)),'delay_bins':h,'delivery_lag_bins':lag,
                    'method':method,'raw':p,'corrected':corrected,'feedback_count':n,
                    'normalized_delta':delta,'last_feedback_available_bin':available}))
            chosen=min(METHODS[1:],key=lambda name:score(target,preds[name],val)['pinball'])
            selections[f'{h}:{lag}']={'baseline':chosen,'validation_scores':{name:score(target,p,val) for name,p in preds.items()}}
            packs[(h,lag)]=(target,raw,preds,chosen)
    # Selection is persisted before scoring the test. The entire test is already exposed.
    save(out/'SELECTION.json',{'validation_only':selections,'saved_before_new_test_scores':True})
    pd.concat(rows,ignore_index=True).to_parquet(out/'replay.parquet',index=False)
    results={}
    for (h,lag),(target,raw,preds,chosen) in packs.items():
        ids=np.arange(1152,1440-h)
        scores={name:score(target,p,ids) for name,p in preds.items()}
        raw_scores={name:score(target,p,ids) for name,p in raw.items()}
        a,b=scores['hgb'],scores[chosen]
        gain=1-a['pinball']/b['pinball']
        gate='EXPLORATORY_PASS' if gain>=.05 and .85<=a['coverage']<=.95 else 'NO_GO'
        results[f'{h}:{lag}']={'ready_delay_minutes':5*h,'assumed_feedback_delay_minutes':5*lag,
            'primary':lag==0,'chosen_baseline':chosen,'scores':scores,'raw_scores':raw_scores,
            'model_gain_vs_corrected_baseline':gain,
            'model_gain_vs_raw_model':1-a['pinball']/raw_scores['hgb']['pinball'], 'gate':gate}
    save(out/'results.json',{'comparisons':results,'new_predictive_models_trained':0,
        'completed_label_calibration_only':True,'test_previously_seen':True,'operational_adoption':False})
    check(stage)
    print(json.dumps({k:{n:v[n] for n in ('chosen_baseline','model_gain_vs_corrected_baseline','model_gain_vs_raw_model','gate')} for k,v in results.items()},indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','run']);ap.add_argument('--stage',required=True,type=Path)
    args=ap.parse_args();{'prepare':prepare,'run':run}[args.mode](args.stage)
