"""Audit complete training, validation selection, mature feedback and policy replay."""
import argparse
from dataclasses import asdict
import itertools
import json
import math
import sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from delay_capacity_policy import peak_targets,mature_feedback,Hold,replay_delay,choose_hold
from demand_capacity_policy import summarize,Config,replay
from train_evaluate_delay_capacity import sha,design,training_origins,PARAMS,FEATURES,BASELINES,pinball,save_json


def assert_metrics(a,b):
    for k,v in a.items():np.testing.assert_allclose(v,b[k],rtol=1e-12,atol=1e-6)


def verify(root,out):
    manifest=json.loads((out/'manifest.json').read_text())
    for name,digest in manifest.items():assert sha(out/name)==digest,name
    freeze=json.loads((out/'CODE_FREEZE.json').read_text())
    for path,digest in freeze['inputs'].items():assert sha(path)==digest,path
    for path,digest in freeze['code'].items():
        source=(Path(__file__).parents[1]/'tests'/Path(path).name if Path(path).name.startswith('test_')
                else Path(__file__).parents[1]/'reports'/Path(path).name if Path(path).suffix=='.json'
                else Path(__file__).with_name(Path(path).name))
        assert sha(source)==digest,source
    result=json.loads((out/'results.json').read_text())
    selection=json.loads((out/'SELECTION_FREEZE.json').read_text())
    assert len(selection['model_hashes'])==6 and len(selection['policy_selections'])==90
    assert len(result['scenarios'])==180
    selected={(x['dataset'],x['delay'],x['capacity_factor'],x['method']):x for x in selection['policy_selections']}
    traces=pd.read_csv(out/'decision_replay.csv.gz')
    assert len(traces)==180*864
    assert not traces.duplicated(['dataset','delay','capacity_factor','method','variant','absolute_bin']).any()
    sys.path.insert(0,str(root/'scripts'))
    import evaluate_azure_code_input_demand as coding
    retrained=0;verified_selection=0
    for name in ('coding','lmm'):
        old=pd.read_parquet(root/'artifacts/demand_capacity_v1'/f'{name}_aggregate_and_forecasts.parquet')
        y=old.y.to_numpy(float);frame=design(y,name,coding);m=frame.scale.to_numpy(float)
        X=frame[list(FEATURES)].to_numpy(float)
        mutated=y.copy();mutated[1152:]=1e12
        altered_frame=design(mutated,name,coding)
        np.testing.assert_allclose(X[:1152],altered_frame[list(FEATURES)].to_numpy(float)[:1152],equal_nan=True)
        for delay in (1,2,6):
            file=out/f'{name}_delay{delay}_peak_q90.joblib'
            assert sha(file)==selection['model_hashes'][file.name]
            bundle=joblib.load(file);model=bundle['model'];train=training_origins(delay)
            assert len(train)==852-delay and train[-1]+delay==863
            np.testing.assert_array_equal(bundle['train_origins'],train)
            assert bundle['features']==FEATURES and bundle['last_training_label_available_bin']==864
            target=peak_targets(y,delay);altered_target=peak_targets(mutated,delay)
            np.testing.assert_array_equal(target[train],altered_target[train])
            refitted=HistGradientBoostingRegressor(**PARAMS)
            refitted.fit(X[train],target[train]/m[train],sample_weight=m[train]/m[train].mean())
            np.testing.assert_array_equal(refitted.predict(X),model.predict(X));retrained+=1
            arrays=pd.read_parquet(out/f'{name}_delay{delay}_forecasts.parquet')
            np.testing.assert_array_equal(arrays.y.to_numpy(float),y)
            np.testing.assert_array_equal(arrays.peak_target.to_numpy(float),target)
            raw=np.maximum(0.,model.predict(X))*m
            np.testing.assert_array_equal(arrays.peak_model_raw.to_numpy(float),raw)
            corrected=mature_feedback(target,raw,m,delay)
            np.testing.assert_array_equal(arrays.peak_model_feedback.to_numpy(float),corrected)
            changed_correction=mature_feedback(altered_target,raw,m,delay)
            np.testing.assert_array_equal(corrected[:1153],changed_correction[:1153])
            val=np.arange(864,1152-delay)
            ratio=coding.weighted_quantile(target[train]/m[train],m[train])
            baselines={'train_weighted_peak_ratio_q90':ratio*m,
                       'past_hour_max':frame.past_hour_max_over_scale.to_numpy(float)*m,
                       'past_hour_empirical_q90':frame.past_hour_empirical_q90.to_numpy(float),
                       'past_hour_mean':m}
            baseline=min(BASELINES,key=lambda k:float(pinball(target[val],baselines[k][val]).mean()))
            assert result['forecasts'][f'{name}_delay{delay}']['baseline']==baseline
            np.testing.assert_array_equal(arrays.peak_baseline_feedback.to_numpy(float),mature_feedback(target,baselines[baseline],m,delay))
            for factor in (.5,1.,2.):
                capacity=float(np.median(y[12:864])/2*factor)
                initial=min(8,max(1,math.ceil(np.median(y[12:864])/capacity)))
                methods=['v1_model_feedback','v1_selected_baseline_feedback','peak_model_raw','peak_model_feedback','peak_baseline_feedback']
                for method in methods:
                    p=arrays[method].to_numpy(float)
                    sel=selected[(name,delay,factor,method)]
                    records=[]
                    for dwell,confirm,headroom in itertools.product((0,6,12),(3,6),(.6,.8)):
                        hold=Hold(dwell,confirm,headroom)
                        metrics=summarize(replay_delay(y[864:1152],p[864:1152],capacity,delay,initial,hold))
                        record={'hold':asdict(hold),'metrics':metrics};records.append(record)
                        frozen=next(x for x in sel['candidates'] if x['hold']==record['hold'])
                        assert_metrics(metrics,frozen['metrics'])
                    assert choose_hold(records)['hold']==sel['selected_hold'];verified_selection+=1
                    default=replay(y[864:],p[864:],Config(capacity,startup_bins=delay),initial)
                    for variant in ('default_hold','validation_selected_hold'):
                        hold=Hold() if variant=='default_hold' else Hold(**sel['selected_hold'])
                        rows=replay_delay(y[864:],p[864:],capacity,delay,initial,hold,hold_start=288)
                        for field in ('action','active_slots','pending_ready_bin','requested_slots','backlog_tokens'):
                            assert [r[field] for r in rows[:288]]==[r[field] for r in default[:288]]
                        summary=next(r for r in result['scenarios'] if (r['dataset'],r['delay'],r['capacity_factor'],r['method'],r['variant'])==(name,delay,factor,method,variant))
                        assert_metrics(summarize(rows[288:]),summary)
                        part=traces[(traces.dataset==name)&(traces.delay==delay)&(traces.capacity_factor==factor)&(traces.method==method)&(traces.variant==variant)]
                        assert list(part.absolute_bin)==list(range(1152,2016))
                        assert part.active_slots.between(1,8).all() and part.requested_slots.between(1,8).all()
                        for field in ('active_slots','requested_slots','backlog_tokens','capacity_tokens'):
                            np.testing.assert_allclose(part[field],[r[field] for r in rows[288:]],rtol=1e-12,atol=1e-5)
                        assert list(part.action)==[r['action'] for r in rows[288:]]
                        changed=replay_delay(mutated[864:],p[864:],capacity,delay,initial,hold,hold_start=288)
                        for field in ('action','active_slots','pending_ready_bin','requested_slots'):
                            assert [r[field] for r in rows[:289]]==[r[field] for r in changed[:289]]
    audit={'status':'VERIFIED_EXPLORATORY_TRAINING_AND_REPLAY','retrained_exact_models':retrained,
           'validation_selections_recomputed':verified_selection,'validation_candidates_checked':1080,
           'assessment_scenarios_replayed':180,'assessment_decision_rows_checked':len(traces),
           'training_targets_finish_before_validation':True,'assessment_values_do_not_change_training_or_validation_features':True,
           'feedback_labels_mature_before_use':True,'selected_hold_not_applied_retroactively_to_validation':True,
           'actual_vm_or_gpu_or_user_slo_verified':False}
    save_json(out/'audit.json',audit)
    print(json.dumps(audit,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--research-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();verify(args.research_root,args.output)
