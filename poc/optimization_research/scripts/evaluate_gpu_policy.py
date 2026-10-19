"""Workload-level conformal calibration for measured GPU shape selection.

The dataset is a static benchmark: this evaluates a proposed configuration
against its independently measured outcome, not a deployment or scale event.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.stats import beta

from train_performance import ROOT, PRIMARY, FEATURES, TARGETS, assign_splits, make_model, support_mask

ALPHA = 0.10
RESOURCE_COLUMNS = ['replica_count', 'cpu_core_limit']
EXPECTED_SLATE = {(replicas, cores) for replicas in (1, 2) for cores in (2, 4, 8, 16)}


def validate_slate(frame, require_all_groups=True):
    if not len(frame):
        raise ValueError('No candidate rows')
    if frame[['latency_slo_ms','target_throughput_rps','batch_size']].isna().any().any():
        raise ValueError('Missing SLO or batch size')
    if (frame[['latency_slo_ms','target_throughput_rps','batch_size']] <= 0).any().any():
        raise ValueError('SLO and batch size must be positive')
    for group, part in frame.groupby('workload_group'):
        if part[['latency_slo_ms','target_throughput_rps','batch_size']].nunique().max() != 1:
            raise ValueError(f'Inconsistent SLO within group {group}')
        observed = set(map(tuple, part[RESOURCE_COLUMNS].to_numpy()))
        if require_all_groups and (len(part) != 8 or observed != EXPECTED_SLATE):
            raise ValueError(f'Incomplete GPU slate for group {group}')


def fit_calibration_split(frame, seed):
    """Move 25% of each model's original train groups to fresh calibration."""
    original = assign_splits(frame, seed)
    rng = np.random.default_rng(seed + 101)
    assignment = pd.Series(original.to_numpy(), index=frame.index)
    for _, part in frame[original == 'train'].groupby('model_id', sort=True):
        groups = np.array(sorted(part.workload_group.unique()))
        rng.shuffle(groups)
        n = max(1, int(math.ceil(len(groups) * .25)))
        assignment.loc[part.index[part.workload_group.isin(groups[:n])]] = 'calibration'
    if set(assignment) != {'train','calibration','validation','test'}:
        raise ValueError('Missing split')
    if frame.groupby('workload_group').apply(lambda g: assignment.loc[g.index].nunique(),
                                               include_groups=False).max() != 1:
        raise ValueError('Workload group leaked between splits')
    return assignment


def fit_predict(frame, assignments, seed):
    fit = frame[assignments == 'train']
    model = make_model('extra_trees', seed)
    weights = 1 / fit.groupby('workload_group').workload_group.transform('size')
    weights = weights / weights.mean()
    model.fit(fit[FEATURES], np.log(fit[TARGETS]), regressor__sample_weight=weights)
    result=annotate_predictions(model,frame,assignments)
    return model, result


def annotate_predictions(model, frame, assignments):
    result = frame.copy()
    result['split'] = assignments
    predicted = np.exp(model.predict(result[FEATURES]))
    result['predicted_latency_ms'] = predicted[:, 0]
    result['predicted_requests_per_second'] = predicted[:, 1] / result.batch_size
    result['measured_requests_per_second'] = result.examples_per_second / result.batch_size
    # Fixed, outcome-free local scale for exploratory adaptive calibration.
    transformed=model.named_steps['features'].transform(result[FEATURES])
    forest=model.named_steps['regressor']
    tree_predictions=np.stack([tree.predict(transformed) for tree in forest.estimators_])
    dispersion=tree_predictions.std(axis=0)
    result['scale_latency_log']=0.10+dispersion[:,0]
    result['scale_throughput_log']=0.10+dispersion[:,1]
    return result


def one_sided_residuals(frame):
    latency = np.log(frame.latency_p95_ms / frame.predicted_latency_ms)
    throughput = np.log(frame.predicted_requests_per_second / frame.measured_requests_per_second)
    return np.maximum(latency, throughput)


def conformal_quantile(scores, alpha):
    """Split-conformal order statistic; infinity if sample count is too small."""
    values = np.sort(np.asarray(scores, dtype=float))
    if not len(values) or not np.isfinite(values).all() or not 0 < alpha < 1:
        raise ValueError('Invalid calibration data or alpha')
    index = int(math.ceil((len(values) + 1) * (1 - alpha))) - 1
    return float(values[index]) if index < len(values) else math.inf


def group_calibration_scores(calibration):
    scored = calibration[['workload_group']].copy()
    scored['score'] = one_sided_residuals(calibration)
    return scored.groupby('workload_group').score.max()


def adaptive_group_scores(calibration):
    scored=calibration[['workload_group']].copy()
    latency=np.log(calibration.latency_p95_ms/calibration.predicted_latency_ms)/calibration.scale_latency_log
    throughput=np.log(calibration.predicted_requests_per_second/calibration.measured_requests_per_second)/calibration.scale_throughput_log
    scored['score']=np.maximum(latency,throughput)
    return scored.groupby('workload_group').score.max()


def cost_key(row):
    return (int(row.replica_count), int(row.cpu_core_limit))


def measured_feasible(frame):
    return ((frame.latency_p95_ms <= frame.latency_slo_ms)
            & (frame.measured_requests_per_second >= frame.target_throughput_rps))


def predicted_feasible(frame, q, adaptive=False):
    lag_scale=frame.scale_latency_log if adaptive else 1.0
    rate_scale=frame.scale_throughput_log if adaptive else 1.0
    return ((frame.predicted_latency_ms * np.exp(q*lag_scale) <= frame.latency_slo_ms)
            & (frame.predicted_requests_per_second / np.exp(q*rate_scale) >= frame.target_throughput_rps))


def select(frame, policy, q=0.0):
    if policy == 'oracle':
        eligible = frame[measured_feasible(frame)]
    elif policy == 'cheapest':
        eligible = frame
    else:
        eligible = frame[predicted_feasible(frame, q, policy=='adaptive_group_90')]
    if len(eligible) == 0:
        return None
    return min(eligible.itertuples(), key=cost_key)


def policy_records(frame, thresholds):
    rows = []
    for group_id, slate in frame.groupby('workload_group', sort=True):
        oracle = select(slate, 'oracle')
        for policy in ['cheapest', 'point', 'row_conformal_90', 'group_conformal_80', 'group_conformal_90', 'adaptive_group_90', 'oracle']:
            chosen = select(slate, policy, thresholds.get(policy, 0.0))
            actual = None if chosen is None else bool(
                chosen.latency_p95_ms <= chosen.latency_slo_ms
                and chosen.measured_requests_per_second >= chosen.target_throughput_rps)
            rows.append({'workload_group':group_id,'model_id':slate.model_id.iloc[0],
                         'policy':policy,'selected':chosen is not None,
                         'success':actual,'violation':int(chosen is not None and not actual),
                         'oracle_feasible':oracle is not None,
                         'gpu_count':None if chosen is None else chosen.replica_count,
                         'cpu_cores':None if chosen is None else chosen.cpu_core_limit,
                         'oracle_gpu_count':None if oracle is None else oracle.replica_count,
                         'oracle_cpu_cores':None if oracle is None else oracle.cpu_core_limit,
                         'execution_id':None if chosen is None else chosen.execution_id,
                         'true_latency_ms':None if chosen is None else chosen.latency_p95_ms,
                         'true_requests_per_second':None if chosen is None else chosen.measured_requests_per_second,
                         'latency_slo_ms':slate.latency_slo_ms.iloc[0],
                         'throughput_slo_rps':slate.target_throughput_rps.iloc[0]})
    return pd.DataFrame(rows)


def proportion_ci(k, n):
    if n == 0:
        return [None, None]
    lo = 0.0 if k == 0 else float(beta.ppf(.025, k, n-k+1))
    hi = 1.0 if k == n else float(beta.ppf(.975, k+1, n-k))
    return [lo, hi]


def summarize(records):
    output = {}
    n = records.workload_group.nunique()
    for policy, part in records.groupby('policy'):
        selected = part[part.selected]
        successful = selected[selected.success == True]
        oracle_reachable = part.oracle_feasible.sum()
        output[policy] = {
            'groups':int(n), 'selected':int(len(selected)),
            'selection_rate':float(len(selected)/n),
            'violations':int(part.violation.sum()),
            'joint_selection_and_violation_rate':float(part.violation.mean()),
            'selected_violation_rate':None if not len(selected) else float(selected.violation.mean()),
            'selected_violation_95_percent_ci':proportion_ci(int(part.violation.sum()),len(selected)),
            'oracle_feasible_groups':int(oracle_reachable),
            'feasible_and_successfully_selected':int(len(successful[successful.oracle_feasible])),
            'feasible_group_success_rate':None if not oracle_reachable else float(len(successful[successful.oracle_feasible])/oracle_reachable),
            'mean_gpu_count_selected':None if not len(selected) else float(selected.gpu_count.mean()),
            'mean_cpu_affinity_cores_selected':None if not len(selected) else float(selected.cpu_cores.mean()),
            'mean_gpu_excess_vs_oracle_on_successful_choices':None if not len(successful) else float(
                (successful.gpu_count-successful.oracle_gpu_count).mean()),
            'mean_core_excess_vs_oracle_on_successful_same_gpu_choices':None,
        }
        same_gpu=successful[successful.gpu_count == successful.oracle_gpu_count]
        if len(same_gpu):
            output[policy]['mean_core_excess_vs_oracle_on_successful_same_gpu_choices']=float(
                (same_gpu.cpu_cores-same_gpu.oracle_cpu_cores).mean())
    return output


def coverage(frame, q):
    scores=frame[['workload_group']].copy()
    scores['score']=one_sided_residuals(frame)
    maxima=scores.groupby('workload_group').score.max()
    return {'groups':len(maxima), 'simultaneous_coverage':float((maxima <= q).mean()),
            'covered_groups':int((maxima <= q).sum()),
            '95_percent_ci':proportion_ci(int((maxima <= q).sum()),len(maxima))}


def paired_bootstrap(records, left='point', right='group_conformal_90', seed=42):
    # Difference in joint violation and selection rates, sampled at workload level.
    x=records[records.policy==left].sort_values('workload_group')
    y=records[records.policy==right].sort_values('workload_group')
    assert x.workload_group.tolist()==y.workload_group.tolist()
    rng=np.random.default_rng(seed)
    n=len(x)
    take=rng.integers(0,n,size=(4000,n))
    output={}
    for col in ['violation','selected']:
        delta=x[col].to_numpy(dtype=float)-y[col].to_numpy(dtype=float)
        boot=delta[take].mean(axis=1)
        output[f'{left}_minus_{right}_{col}_rate']={
            'estimate':float(delta.mean()),'bootstrap_95_percent_ci':np.quantile(boot,[.025,.975]).tolist()}
    return output


def run(snapshot, output, seed=42):
    output.mkdir(parents=True,exist_ok=True)
    raw=pd.read_parquet(snapshot)
    primary=raw[(raw.campaign==PRIMARY)&raw.eligible&(raw.device=='cuda')].copy()
    validate_slate(primary)
    primary['split']=fit_calibration_split(primary,seed)
    split=primary.pop('split')
    model, data=fit_predict(primary,split,seed)
    calibration=data[data.split=='calibration'].copy()
    test=data[data.split=='test'].copy()
    group_scores=group_calibration_scores(calibration)
    adaptive_scores=adaptive_group_scores(calibration)
    row_scores=one_sided_residuals(calibration)
    qs={'point':0.0,
        'row_conformal_90':conformal_quantile(row_scores,.10),
        'group_conformal_80':conformal_quantile(group_scores,.20),
        'group_conformal_90':conformal_quantile(group_scores,.10),
        'adaptive_group_90':conformal_quantile(adaptive_scores,.10)}
    observed=policy_records(test,qs)
    observed.to_csv(output/'choices_test.csv',index=False)
    data[['execution_id','workload_group','model_id','split','replica_count','cpu_core_limit',
          'latency_p95_ms','examples_per_second','batch_size','latency_slo_ms',
          'target_throughput_rps','predicted_latency_ms','predicted_requests_per_second',
          'measured_requests_per_second','scale_latency_log','scale_throughput_log']].to_parquet(output/'predictions.parquet',index=False)
    joblib.dump(model,output/'gpu_performance_model.joblib')
    fit=data[data.split=='train']
    external={}
    for campaign in ['khu-hf-local-novel-matrix','khu-hf-local-space-filling']:
        source=raw[(raw.campaign==campaign)&raw.eligible&(raw.device=='cuda')].copy()
        validate_slate(source)
        independent=~source.workload_group.isin(data.workload_group)
        support=support_mask(fit,source)
        complete_support=source.assign(independent=independent,supported=support).groupby('workload_group')[
            ['independent','supported']].all().all(axis=1)
        supported_groups=set(complete_support[complete_support].index)
        subset=source[source.workload_group.isin(supported_groups)]
        if len(subset):
            forecast=annotate_predictions(model,subset,pd.Series('external',index=subset.index))
            policy=policy_records(forecast,qs)
            policy.to_csv(output/f'choices_{campaign}.csv',index=False)
            external[campaign]={'total_groups':int(source.workload_group.nunique()),
                                'supported_independent_groups':int(len(supported_groups)),
                                'simultaneous_coverage_90':coverage(forecast,qs['group_conformal_90']),
                                'adaptive_simultaneous_coverage_90':float((adaptive_group_scores(forecast)<=qs['adaptive_group_90']).mean()),
                                'policies':summarize(policy),
                                'paired_bootstrap':paired_bootstrap(policy)}
        else:
            external[campaign]={'total_groups':int(source.workload_group.nunique()),
                                'supported_independent_groups':0}
    result={'experiment':'group-simultaneous SLO calibration for GPU configurations',
            'seed':seed,'nominal_alpha':ALPHA,'source_snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model_sha256':hashlib.sha256((output/'gpu_performance_model.joblib').read_bytes()).hexdigest(),
            'python_version':platform.python_version(),'sklearn_version':sklearn.__version__,
            'fit_kind':'extra_trees_preselected_from_prior_experiment',
            'split':{name:{'rows':int((data.split==name).sum()),
                            'groups':int(data.loc[data.split==name,'workload_group'].nunique())}
                     for name in ['train','calibration','validation','test']},
            'calibration':{'group_count':len(group_scores),'row_count':len(row_scores),
                           'thresholds':qs,'adaptive_scale':'0.10 + per-tree prediction standard deviation in log space'},
            'test_simultaneous_coverage_90':coverage(test,qs['group_conformal_90']),
            'test_adaptive_simultaneous_coverage_90':{
                'groups':int(test.workload_group.nunique()),
                'covered_groups':int((adaptive_group_scores(test)<=qs['adaptive_group_90']).sum()),
                'rate':float((adaptive_group_scores(test)<=qs['adaptive_group_90']).mean())},
            'test_policies':summarize(observed),
            'paired_bootstrap':paired_bootstrap(observed),
            'external_stress_tests':external,
            'slo_units':'target_throughput_rps in requests/s; observed examples_per_second divided by batch_size',
            'resource_order':'minimize GPU count first, then CPU affinity cores; no price data',
            'scope':'single host, synthetic benchmark SLOs, 8 completely observed GPU configs, one execution per config'}
    (output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'splits':result['split'],'calibration':result['calibration'],
                      'coverage':result['test_simultaneous_coverage_90'],
                      'policies':result['test_policies']},indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',type=Path,default=ROOT/'data/audit_v1/measurements.parquet')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/gpu_policy_v1')
    parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args()
    run(args.snapshot,args.output,args.seed)
