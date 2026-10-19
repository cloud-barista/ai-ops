"""Refit the three explicit training splits; replay stored forecasts and metrics."""
import argparse
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from train_copilot_call_demand import design,candidates
from train_evaluate_delay_capacity import FEATURES,PARAMS,sha


def verify(stage):
    out=stage/'artifacts/copilot_call_demand_v1';source=stage/'artifacts/copilot_transfer_v1_1'
    freeze=json.loads((out/'CODE_FREEZE.json').read_text());inputfreeze=json.loads((out/'INPUT_FREEZE.json').read_text())
    for folder,names in (('scripts',('train_copilot_call_demand.py','verify_copilot_call_demand.py')),('tests',('test_copilot_call_demand.py',)),('reports',('copilot_call_demand_protocol_v1.json',))):
        for n in names:assert sha(stage/folder/n)==freeze['code'][n]
    assert sha(source/'complete_call_counts_and_known_tokens.parquet')==inputfreeze['input_sha256']
    assert sha(source/'independent_source_audit.json')==inputfreeze['source_audit_sha256']
    for n,digest in freeze['helpers'].items():assert sha(Path('/home/researcher/ai-mcmp/research/scripts')/n)==digest
    selections=json.loads((out/'SELECTION_FREEZE.json').read_text());results=json.loads((out/'results.json').read_text())
    data=pd.read_parquet(source/'complete_call_counts_and_known_tokens.parquet');saved=pd.read_parquet(out/'forecasts.parquet')
    y=data.recorded_llm_calls.to_numpy(float);np.testing.assert_array_equal(y,saved.observed_calls)
    frame=design(y);m=frame.scale.to_numpy(float);X=frame[list(FEATURES)].to_numpy(float)
    np.testing.assert_allclose(m,saved.scale,rtol=0,atol=0,equal_nan=True)
    for h in (1,2,6):
        path=out/f'copilot_delay{h}_call_peak_q90.joblib';assert sha(path)==selections['model_hashes'][path.name]
        bundle=joblib.load(path);target=np.full(864,np.nan)
        for t in range(864-h):target[t]=max(y[t:t+h+1])
        np.testing.assert_allclose(target,saved[f'd{h}_target'],rtol=0,atol=0,equal_nan=True)
        train=np.arange(12,288-h);val=np.arange(288,576-h);test=np.arange(576,864-h)
        np.testing.assert_array_equal(train,bundle['train_origins']);assert train[-1]+h+1==288 and val[-1]+h+1==576
        assert bundle['delay']==h and bundle['features']==FEATURES and bundle['parameters']==PARAMS
        fresh=HistGradientBoostingRegressor(**PARAMS)
        fresh.fit(X[train],target[train]/m[train],sample_weight=m[train]/m[train].mean())
        raw=np.maximum(0,fresh.predict(X))*m
        np.testing.assert_allclose(raw,saved[f'd{h}_model'],rtol=0,atol=0,equal_nan=True)
        np.testing.assert_array_equal(fresh.predict(X),bundle['model'].predict(X))
        base=candidates(y,m,frame,h)
        def loss(p,ids):
            e=target[ids]-p[ids];return float(np.where(e>=0,.9*e,-.1*e).mean())
        chosen=min(base,key=lambda k:loss(base[k],val))
        assert chosen==selections['validation_only_baselines'][str(h)]['baseline']
        for name,v in base.items():
            np.testing.assert_allclose(v,saved[f'd{h}_{name}'],rtol=0,atol=0,equal_nan=True)
            np.testing.assert_allclose(loss(v,test),results['methods'][str(h)]['all_baseline_test_pinball'][name],rtol=1e-14)
        lp=loss(raw,test);lb=loss(base[chosen],test);cov=float(np.mean(target[test]<=raw[test]));gain=1-lp/lb
        for k,v in {'test_rows':len(test),'pinball':lp,'baseline_pinball':lb,'relative_loss_reduction':gain,'coverage':cov}.items():
            np.testing.assert_allclose(v,results['methods'][str(h)][k],rtol=1e-14)
        passed=gain>=.05 and .85<=cov<=.95
        assert results['methods'][str(h)]['gate']==('EXPLORATORY_CALL_FORECAST_PASS' if passed else 'NO_GO')
    mutant=y.copy();mutant[576:]=10**6
    np.testing.assert_allclose(design(y)[list(FEATURES)].iloc[:577],design(mutant)[list(FEATURES)].iloc[:577],rtol=0,atol=0,equal_nan=True)
    assert results['old_token_transfer_quality_no_go_preserved'] and not results['operational_adoption']
    audit={'status':'VERIFIED','models_refit_exact':3,'manual_peak_target_replay':True,
        'train_validation_label_completion_boundaries_checked':True,'baseline_selection_and_all_test_metrics_replayed':True,
        'test_future_mutation_does_not_change_training_validation_or_first_test_features':True,
        'source_input_and_code_hashes_checked':True,'token_transfer_no_go_unchanged':True}
    with (out/'independent_model_audit.json').open('x') as f:json.dump(audit,f,indent=2)
    print(json.dumps(audit))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--stage',type=Path,required=True);p=a.parse_args();verify(p.stage)
