"""Compare unseen-model response methods and verified second-probe budgets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from train_performance import ROOT

BASE_METHODS=['zero_shot','anchor_shift','ratio_template','relative_prior']
NEW_METHODS=['anchor_features_ablation','anchor_conditioned','anchor_conditioned_slo_weighted','anchor_conditioned_no_prior']
PRIMARY='khu-hf-local-exhaustive'


def load_predictions():
    base_path=ROOT/'artifacts/novel_transfer_v1/predictions.parquet'
    new_path=ROOT/'artifacts/conditional_response_v1/predictions.parquet'
    base=pd.read_parquet(base_path)
    measured=pd.read_parquet(ROOT/'data/audit_v1/measurements.parquet',columns=['execution_id','compute_seconds','wall_seconds'])
    if measured.execution_id.duplicated().any():
        raise ValueError('Duplicate execution IDs in frozen measurements')
    base=base.merge(measured,on='execution_id',validate='one_to_one')
    new=pd.read_parquet(new_path)
    frame=new[['execution_id','campaign','method','predicted_log_latency',
               'predicted_log_requests_per_second']].copy()
    if frame.duplicated(['execution_id','campaign','method']).any():
        raise ValueError('Duplicate proposed predictions')
    combined=[]
    for method in BASE_METHODS:
        part=base.copy()
        part['method']=method
        part['predicted_log_latency']=part[f'pred_{method}_log_latency_p95_ms']
        part['predicted_log_requests_per_second']=part[f'pred_{method}_log_requests_per_second']
        combined.append(part)
    for method in NEW_METHODS:
        part=base.merge(frame[frame.method==method],on=['execution_id','campaign'],validate='one_to_one')
        part['method']=method
        combined.append(part)
    pred=pd.concat(combined,ignore_index=True)
    if pred[['predicted_log_latency','predicted_log_requests_per_second']].isna().any().any():
        raise ValueError('Missing prediction')
    # Every method sees the measured anchor. Baselines that do not use it for
    # prediction still receive the same one-probe budget before selection.
    anchor=pred.is_anchor
    pred.loc[anchor,'predicted_log_latency']=pred.loc[anchor,'true_log_latency_p95_ms']
    pred.loc[anchor,'predicted_log_requests_per_second']=pred.loc[anchor,'true_log_requests_per_second']
    return pred, {'baseline_sha256':hashlib.sha256(base_path.read_bytes()).hexdigest(),
                  'proposed_sha256':hashlib.sha256(new_path.read_bytes()).hexdigest()}


def evaluate_prediction(pred,seed=42):
    unprobed=pred[~pred.is_anchor].copy()
    unprobed['latency_abs_log_error']=(unprobed.predicted_log_latency-unprobed.true_log_latency_p95_ms).abs()
    unprobed['throughput_abs_log_error']=(unprobed.predicted_log_requests_per_second-unprobed.true_log_requests_per_second).abs()
    by_model=unprobed.groupby(['campaign','method','model_id']).agg(
        groups=('workload_group','nunique'),candidate_rows=('execution_id','size'),
        latency_mae_log=('latency_abs_log_error','mean'),
        throughput_mae_log=('throughput_abs_log_error','mean')).reset_index()
    summary={}
    for (campaign,method),part in by_model.groupby(['campaign','method']):
        summary[f'{campaign}/{method}']={
            'model_count':len(part),'workload_groups':int(part.groups.sum()),
            'latency_macro_mae_log':float(part.latency_mae_log.mean()),
            'throughput_macro_mae_log':float(part.throughput_mae_log.mean())}
    # Paired model-level uncertainty; rows within one model are correlated.
    comparisons={}
    for campaign in [PRIMARY,'khu-hf-local-novel-matrix','khu-hf-local-space-filling']:
        part=by_model[by_model.campaign==campaign]
        table=part.pivot(index='model_id',columns='method',values=['latency_mae_log','throughput_mae_log'])
        for method in ['anchor_conditioned','anchor_conditioned_slo_weighted',
                       'anchor_features_ablation','relative_prior','ratio_template']:
            if len(table)==0: continue
            item={}
            for metric in ['latency_mae_log','throughput_mae_log']:
                delta=(table[(metric,'anchor_shift')]-table[(metric,method)]).dropna().to_numpy()
                rng=np.random.default_rng(seed)
                samples=delta[rng.integers(0,len(delta),size=(4000,len(delta)))].mean(axis=1)
                item[metric]={'shift_minus_method':float(delta.mean()),
                    'model_bootstrap_95_percent_ci':np.quantile(samples,[.025,.975]).tolist(),
                    'improved_models':int((delta>0).sum()),'models':len(delta)}
            comparisons[f'{campaign}/{method}']=item
    return by_model,summary,comparisons


def choose_group(group):
    true_feasible=(group.latency_p95_ms<=group.latency_slo_ms)&(
        group.examples_per_second/group.batch_size>=group.target_throughput_rps)
    pred_feasible=(group.predicted_log_latency<=np.log(group.latency_slo_ms))&(
        group.predicted_log_requests_per_second>=np.log(group.target_throughput_rps))
    proposed=group[pred_feasible].sort_values(['replica_count','cpu_core_limit'])
    oracle=group[true_feasible].sort_values(['replica_count','cpu_core_limit'])
    selected=proposed.iloc[0] if len(proposed) else None
    feasible=oracle.iloc[0] if len(oracle) else None
    anchor=group[group.is_anchor].iloc[0]
    anchor_pass=bool(anchor.latency_p95_ms<=anchor.latency_slo_ms and
                     anchor.examples_per_second/anchor.batch_size>=anchor.target_throughput_rps)
    good=bool(selected is not None and selected.latency_p95_ms<=selected.latency_slo_ms and
              selected.examples_per_second/selected.batch_size>=selected.target_throughput_rps)
    second=group[(group.replica_count==2)&(group.cpu_core_limit==2)].iloc[0]
    second_pass=bool(second.latency_p95_ms<=second.latency_slo_ms and
        second.examples_per_second/second.batch_size>=second.target_throughput_rps)
    slack=min(np.log(second.latency_slo_ms)-second.predicted_log_latency,
              second.predicted_log_requests_per_second-np.log(second.target_throughput_rps))
    return {'campaign':group.campaign.iloc[0],'method':group.method.iloc[0],
            'model_id':group.model_id.iloc[0],'workload_group':group.workload_group.iloc[0],
            'anchor_pass':anchor_pass,'oracle_feasible':feasible is not None,
            'second_probe_pass':second_pass,'selected':selected is not None,
            'success':good,'violation':bool(selected is not None and not good),
            'selected_gpu':None if selected is None else int(selected.replica_count),
            'oracle_gpu':None if feasible is None else int(feasible.replica_count),
            'second_probe_slack':float(slack),
            'anchor_gpu_seconds':float(anchor.compute_seconds),
            'second_probe_gpu_seconds':float(second.compute_seconds*2),
            'anchor_gpu_wall_seconds':float(anchor.wall_seconds),
            'second_probe_gpu_wall_seconds':float(second.wall_seconds*2)}


def evaluate_decisions(pred):
    rows=[]
    for _,group in pred.groupby(['campaign','method','workload_group'],sort=False):
        if len(group)!=8:
            raise ValueError('Candidate slate incomplete')
        rows.append(choose_group(group))
    chosen=pd.DataFrame(rows)
    summary={}
    for (campaign,method),part in chosen.groupby(['campaign','method']):
        challenge=part[(~part.anchor_pass)&part.oracle_feasible]
        summary[f'{campaign}/{method}']={
            'groups':len(part),'oracle_feasible':int(part.oracle_feasible.sum()),
            'anchor_pass':int(part.anchor_pass.sum()),
            'anchor_failed_but_feasible':len(challenge),
            'selected':int(part.selected.sum()),'success':int(part.success.sum()),
            'violations':int(part.violation.sum()),
            'challenge_success':int(challenge.success.sum()),
            'challenge_violations':int(challenge.violation.sum()),
            'gpu_excess_on_success':float((part.loc[part.success,'selected_gpu']-
                                          part.loc[part.success,'oracle_gpu']).mean())}
    return chosen,summary


def evaluate_probe_budget(chosen):
    primary=chosen[chosen.campaign==PRIMARY]
    part=primary[~primary.anchor_pass].copy()
    n=part.workload_group.nunique()
    possible=int(part[part.method=='anchor_shift'].second_probe_pass.sum())
    if n!=326 or possible!=108:
        raise ValueError(f'Unexpected anchor-fail study population: {n}, positives {possible}')
    result={'anchor_fail_groups':n,'potential_second_probe_successes':possible,
            'random_expected_successes':{},'ranked':{}}
    for budget in [25,50,100,150,200,326]:
        result['random_expected_successes'][str(budget)]=budget*possible/n
    for method,group in part.groupby('method'):
        all_groups=primary[primary.method==method]
        if len(all_groups)!=575 or all_groups.workload_group.nunique()!=575:
            raise ValueError('Expected exactly one anchor for each primary workload')
        anchor_compute_gpu_seconds=float(all_groups.anchor_gpu_seconds.sum())
        anchor_gpu_wall_seconds=float(all_groups.anchor_gpu_wall_seconds.sum())
        ranked=group.sort_values(['second_probe_slack','workload_group'],ascending=[False,True])
        curves={}
        for budget in [25,50,100,150,200,326]:
            attempted=ranked.iloc[:budget]
            curves[str(budget)]={
                'verified_recoveries':int(attempted.second_probe_pass.sum()),
                'recovery_fraction_of_108':float(attempted.second_probe_pass.sum()/possible),
                'benchmark_runs':575+budget,
                'measured_compute_gpu_seconds':float(anchor_compute_gpu_seconds+
                                                     attempted.second_probe_gpu_seconds.sum()),
                'measured_gpu_wall_seconds':float(anchor_gpu_wall_seconds+
                                                  attempted.second_probe_gpu_wall_seconds.sum())}
        threshold=group[group.second_probe_slack>=0]
        result['ranked'][method]={
            'probe_if_predicted_feasible':{'second_probes':len(threshold),
                'verified_recoveries':int(threshold.second_probe_pass.sum()),
                'benchmark_runs':575+len(threshold)},'fixed_budget':curves}
    return result


def main():
    output=ROOT/'artifacts/novel_decision_v1'
    output.mkdir(parents=True,exist_ok=True)
    pred,hashes=load_predictions()
    by_model,predictive,comparisons=evaluate_prediction(pred)
    choice,decisions=evaluate_decisions(pred)
    probes=evaluate_probe_budget(choice)
    by_model.to_csv(output/'prediction_by_model.csv',index=False)
    choice.to_csv(output/'choices.csv',index=False)
    result={'input_hashes':hashes,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'prediction':predictive,'paired_comparisons':comparisons,
            'decision':decisions,'probe_budget':probes,
            'scope':'exploratory heldout-model benchmark; generated SLO; same-host primary'}
    (output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'main_prediction':{k:v for k,v in predictive.items() if k.startswith(PRIMARY)},
                      'main_decision':{k:v for k,v in decisions.items() if k.startswith(PRIMARY)},
                      'probe_100':{k:v['fixed_budget']['100'] for k,v in probes['ranked'].items()}},indent=2))


if __name__=='__main__':
    main()
