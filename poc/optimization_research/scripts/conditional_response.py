"""Cold-start AI resource response: anchor-conditioned relative prediction.

Exploratory research. Outer folds hold out entire model IDs. The only observed
outcome from a held-out workload is its one-GPU, two-core anchor measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

ROOT=Path(__file__).resolve().parents[1]
CAMPAIGNS=['khu-hf-local-exhaustive','khu-hf-local-novel-matrix','khu-hf-local-space-filling']
ANCHOR=(1,2)
BASE_FEATURES=['log_parameter_bytes','log_batch_size','log_max_length',
               'request_concurrency','cpu_core_limit','replica_count','repeats','is_causal_lm']
CONDITIONAL_FEATURES=BASE_FEATURES+['anchor_log_latency','anchor_log_requests_per_second']


def prepare(frame):
    frame=frame[(frame.eligible)&(frame.device=='cuda')].copy()
    expected={(gpu,cores) for gpu in (1,2) for cores in (2,4,8,16)}
    for group, rows in frame.groupby('workload_group',sort=False):
        configs=list(map(tuple,rows[['replica_count','cpu_core_limit']].to_numpy()))
        if len(configs)!=8 or set(configs)!=expected:
            raise ValueError(f'Incomplete or repeated candidate slate: {group}')
        if rows[['model_id','batch_size','max_length','request_concurrency',
                 'latency_slo_ms','target_throughput_rps']].nunique().max()!=1:
            raise ValueError(f'Inconsistent workload metadata: {group}')
    anchor=frame[(frame.replica_count==ANCHOR[0])&(frame.cpu_core_limit==ANCHOR[1])].copy()
    if anchor.workload_group.duplicated().any():
        raise ValueError('One anchor measurement required per workload group')
    anchor['parameter_bytes']=anchor.result_json.map(lambda raw: json.loads(raw)['model_parameter_bytes'])
    anchor['static_task']=anchor.workload_task
    anchor=anchor[['workload_group','latency_p95_ms','examples_per_second','parameter_bytes','static_task']].rename(
        columns={'latency_p95_ms':'anchor_latency_ms','examples_per_second':'anchor_examples_per_second'})
    frame=frame.merge(anchor,on='workload_group',validate='many_to_one')
    if (frame[['latency_p95_ms','examples_per_second','anchor_latency_ms',
               'anchor_examples_per_second','parameter_bytes','batch_size']]<=0).any().any():
        raise ValueError('Measurements and static model size must be positive')
    frame['anchor_log_latency']=np.log(frame.anchor_latency_ms)
    frame['anchor_log_requests_per_second']=np.log(frame.anchor_examples_per_second/frame.batch_size)
    frame['log_parameter_bytes']=np.log(frame.parameter_bytes)
    frame['log_batch_size']=np.log(frame.batch_size)
    frame['log_max_length']=np.log(frame.max_length)
    frame['is_causal_lm']=(frame.static_task=='causal_lm_forward').astype(int)
    frame['true_log_latency']=np.log(frame.latency_p95_ms)
    frame['true_log_requests_per_second']=np.log(frame.examples_per_second/frame.batch_size)
    frame['prior_log_throughput_gain']=np.log(frame.replica_count)
    frame['latency_residual']=frame.true_log_latency-frame.anchor_log_latency
    frame['throughput_residual']=frame.true_log_requests_per_second-frame.anchor_log_requests_per_second-frame.prior_log_throughput_gain
    margin_l=np.abs(np.log(frame.latency_p95_ms/frame.latency_slo_ms))
    margin_t=np.abs(np.log((frame.examples_per_second/frame.batch_size)/frame.target_throughput_rps))
    frame['slo_boundary_margin']=np.minimum(margin_l,margin_t)
    return frame


def fit_model(train,variant,seed):
    if variant not in ['anchor_conditioned','anchor_conditioned_slo_weighted',
                       'anchor_conditioned_no_prior','anchor_features_ablation']:
        raise ValueError(variant)
    features=BASE_FEATURES if variant=='anchor_features_ablation' else CONDITIONAL_FEATURES
    weight=(1+4*np.exp(-train.slo_boundary_margin/.2)).to_numpy() if variant.endswith('slo_weighted') else None
    label_columns=['latency_residual','throughput_residual']
    if variant=='anchor_conditioned_no_prior':
        adjusted=train.throughput_residual+train.prior_log_throughput_gain
        labels=np.stack([train.latency_residual,adjusted],axis=1)
    else:
        labels=train[label_columns].to_numpy()
    model=ExtraTreesRegressor(n_estimators=100,min_samples_leaf=2,max_features=1.,random_state=seed,n_jobs=2)
    model.fit(train[features],labels,sample_weight=weight)
    return model


def predict(model,test,variant):
    features=BASE_FEATURES if variant=='anchor_features_ablation' else CONDITIONAL_FEATURES
    delta=model.predict(test[features])
    predicted=np.stack([test.anchor_log_latency+delta[:,0],
                        test.anchor_log_requests_per_second+delta[:,1]+(
                            0 if variant=='anchor_conditioned_no_prior' else test.prior_log_throughput_gain)],axis=1)
    is_anchor=(test.replica_count==ANCHOR[0])&(test.cpu_core_limit==ANCHOR[1])
    predicted[is_anchor,0]=test.loc[is_anchor,'anchor_log_latency']
    predicted[is_anchor,1]=test.loc[is_anchor,'anchor_log_requests_per_second']
    return predicted


def run(snapshot,output,seed=42):
    output.mkdir(parents=True,exist_ok=True)
    raw=pd.read_parquet(snapshot)
    main=prepare(raw[raw.campaign==CAMPAIGNS[0]])
    external={name:prepare(raw[raw.campaign==name]) for name in CAMPAIGNS[1:]}
    methods=['anchor_features_ablation','anchor_conditioned','anchor_conditioned_slo_weighted',
             'anchor_conditioned_no_prior']
    parts=[]
    for held_model in sorted(main.model_id.unique()):
        train=main[main.model_id!=held_model]
        targets={CAMPAIGNS[0]:main[main.model_id==held_model]}
        targets.update({name:value[value.model_id==held_model] for name,value in external.items()})
        for method in methods:
            model=fit_model(train,method,seed)
            for campaign,fold in targets.items():
                if len(fold)==0: continue
                predicted=predict(model,fold,method)
                out=fold[['campaign','workload_group','execution_id','model_id','replica_count',
                          'cpu_core_limit','batch_size','latency_slo_ms','target_throughput_rps',
                          'anchor_latency_ms','anchor_examples_per_second','compute_seconds',
                          'true_log_latency','true_log_requests_per_second']].copy()
                out['method']=method
                out['predicted_log_latency']=predicted[:,0]
                out['predicted_log_requests_per_second']=predicted[:,1]
                parts.append(out)
            print(f'{held_model} {method} finished',flush=True)
    pred=pd.concat(parts,ignore_index=True)
    pred.to_parquet(output/'predictions.parquet',index=False)
    pred=pred[~((pred.replica_count==ANCHOR[0])&(pred.cpu_core_limit==ANCHOR[1]))].copy()
    pred['latency_absolute_log_error']=(pred.predicted_log_latency-pred.true_log_latency).abs()
    pred['throughput_absolute_log_error']=(pred.predicted_log_requests_per_second-pred.true_log_requests_per_second).abs()
    folds=pred.groupby(['campaign','method','model_id']).agg(
        rows=('execution_id','size'),groups=('workload_group','nunique'),
        latency_mae_log=('latency_absolute_log_error','mean'),
        throughput_mae_log=('throughput_absolute_log_error','mean')).reset_index()
    folds.to_csv(output/'per_model_metrics.csv',index=False)
    result={'source_snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model_split':'leave one model_id out; anchor measurement from each heldout workload only',
            'anchor_config':{'gpu_count':1,'cpu_affinity_cores':2},
            'methods':methods,'parameters':{'trees':100,'leaf_size':2,'seed':seed,
                'weighted_lambda':4,'weighted_tau_log_margin':.2},
            'per_campaign_method':{},'scope':'same-host models; external campaigns new input conditions'}
    for (campaign,method),group in folds.groupby(['campaign','method']):
        result['per_campaign_method'][f'{campaign}/{method}']={
            'models':len(group),'workload_groups':int(group.groups.sum()),
            'unprobed_candidate_rows':int(group.rows.sum()),
            'macro_latency_mae_log':float(group.latency_mae_log.mean()),
            'macro_throughput_mae_log':float(group.throughput_mae_log.mean())}
    (output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['per_campaign_method'],indent=2),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--snapshot',type=Path,default=ROOT/'data/audit_v1/measurements.parquet')
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/conditional_response_v1')
    p.add_argument('--seed',type=int,default=42)
    args=p.parse_args()
    run(args.snapshot,args.output,args.seed)
