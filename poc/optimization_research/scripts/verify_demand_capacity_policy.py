"""Independent artifact replay/temporal audit, without selecting policy settings."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from demand_capacity_policy import Config, replay, summarize, feedback_correct


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root, output):
    result = json.loads((output/'results.json').read_text())
    freeze = json.loads((output/'freeze.json').read_text())
    manifest = json.loads((output/'manifest.json').read_text())
    for name, digest in manifest.items():
        assert sha(output/name) == digest, name
    for name,digest in freeze['code'].items():
        path = Path(__file__).with_name(name) if name != 'test_demand_capacity_policy.py' else Path(__file__).parents[1]/'tests'/name
        assert sha(path) == digest, name
    assert len(result['scenarios']) == 198
    traces = pd.read_csv(output/'decision_replay.csv.gz')
    assert len(traces) == 198 * 864
    assert not traces.duplicated(['dataset','method','capacity_factor','startup_bins','absolute_bin']).any()
    for r in result['scenarios']:
        part = traces[(traces.dataset==r['dataset']) & (traces.method==r['method']) &
                      (traces.capacity_factor==r['capacity_factor']) & (traces.startup_bins==r['startup_bins'])]
        assert list(part.absolute_bin) == list(range(1152,2016))
        assert part.active_slots.between(1,8).all() and part.requested_slots.between(1,8).all()
        np.testing.assert_allclose(part.backlog_tokens, np.maximum(0., part.prior_backlog_tokens+part.observed_tokens-part.capacity_tokens), atol=1e-5,rtol=1e-12)
        if not part.pending_ready_bin.isna().all():
            pending=part.pending_ready_bin.notna()
            assert (part.loc[pending,'pending_ready_bin'] > part.loc[pending,'bin']).all()
        actions=part.loc[part.action.isin(['SCALE_IN','SCALE_OUT']),'bin'].to_numpy()
        assert (np.diff(actions)>=2).all()
        calculated=summarize(part.to_dict('records'))
        for k,v in calculated.items():
            np.testing.assert_allclose(v,r[k],rtol=1e-12,atol=1e-6)
    old_c = json.loads((root/'artifacts/azure_code_input_demand_v1/results.json').read_text())
    old_l = json.loads((root/'artifacts/azure_lmm_demand_transfer_v1/results.json').read_text())
    mapping={'coding':{'model':'relative_q90_hgb'},
             'lmm':{'model':'lmm_modality_relative_q90_hgb','token_only_model':'lmm_token_only_relative_q90_hgb',
                    'coding_transferred_model':'coding_frozen_relative_q90_hgb'}}
    for name,previous,key in [('coding',old_c,'retrospective_assessment'),('lmm',old_l,'assessment')]:
        means=previous[key]['equal_day_mean_absolute_token_pinball']
        metrics=result['diagnostics'][name]['forecast_metrics']
        for method,reference in mapping[name].items():
            np.testing.assert_allclose(metrics[method]['pinball'],means[reference],rtol=1e-12)
    sensitivity=[]
    for name in ('coding','lmm'):
        frame=pd.read_parquet(output/(name+'_aggregate_and_forecasts.parquet'))
        y=frame.y.to_numpy(float); p=frame.model.to_numpy(float); m=frame.scale.to_numpy(float)
        corrected=feedback_correct(y,p,m,864)
        np.testing.assert_array_equal(corrected,frame.model_feedback.to_numpy(float))
        changed=y.copy();changed[1500:]=1e12
        changed_correction=feedback_correct(changed,p,m,864)
        np.testing.assert_array_equal(corrected[:1501],changed_correction[:1501])
        for r in result['scenarios']:
            if r['dataset']!=name:continue
            fixed=r['method']=='fixed_train_q90'
            cap=r['capacity_tokens_per_slot_bin']
            initial=min(8,max(1,int(np.ceil(np.quantile(y[12:864],.9)/cap if fixed else np.median(y[12:864])/cap))))
            forecasts=np.full(len(y),np.quantile(y[12:864],.9)) if fixed else frame[r['method']].to_numpy(float)
            original=replay(y[864:],forecasts[864:],Config(cap,startup_bins=r['startup_bins']),initial,fixed=fixed)
            altered=replay(changed[864:],forecasts[864:],Config(cap,startup_bins=r['startup_bins']),initial,fixed=fixed)
            # At boundary 1500, current outcome can change the resulting queue only.
            upto=1500-864+1
            for field in ('action','reason','active_slots','pending_ready_bin','requested_slots'):
                assert [x[field] for x in original[:upto]] == [x[field] for x in altered[:upto]]
            regenerated=summarize(original[288:])
            for k,v in regenerated.items():np.testing.assert_allclose(v,r[k],rtol=1e-12,atol=1e-6)
        for factor in (.5,1.,2.):
            cap=float(np.median(y[12:864])/2*factor)
            sensitivity.append({'dataset':name,'capacity_factor':factor,
                                'bins_exceeding_max_eight_slot_capacity':int((y[1152:]>8*cap).sum()),
                                'assessment_bins':864})
    audit={'status':'VERIFIED_EXPLORATORY_REPLAY','scenarios':198,'scored_decision_rows':len(traces),
           'all_scenarios_replayed':True,'no_current_or_future_outcome_in_decision':True,
           'feedback_future_mutation_invariant':True,'frozen_forecast_score_parity':True,
           'capacity_saturation':sensitivity,'source_code_freeze_verified':True,
           'actual_user_slo_or_vm_action_verified':False}
    with (output/'audit.json').open('x') as f:json.dump(audit,f,indent=2)
    print(json.dumps(audit,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--research-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();verify(args.research_root,args.output)
