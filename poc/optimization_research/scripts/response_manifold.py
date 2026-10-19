"""Exploratory joint response-surface model for one-anchor GPU/CPU transfer.

Each workload is one training example whose target is the complete vector of
seven latency and seven request-throughput changes relative to its anchor.
Whole model IDs are held out. A low-rank response basis is compared with a
joint unconstrained response regressor under the same one-anchor budget.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import ExtraTreesRegressor

from conditional_response import ROOT, CAMPAIGNS, prepare

CONFIGS=[(gpu,cores) for gpu in (1,2) for cores in (2,4,8,16)]
GROUP_FEATURES=['log_parameter_bytes','log_batch_size','log_max_length',
                'request_concurrency','repeats','is_causal_lm',
                'anchor_log_latency','anchor_log_requests_per_second']
VARIANTS={'joint_direct':None,'joint_pca_2':2,'joint_pca_4':4,'joint_pca_8':8}


def make_groups(frame: pd.DataFrame) -> tuple[pd.DataFrame,np.ndarray]:
    """Build group features and a 14-output surface from a complete slate."""
    groups=[]
    targets=[]
    for group_id,rows in frame.groupby('workload_group',sort=True):
        ordered=rows.set_index(['replica_count','cpu_core_limit']).loc[CONFIGS]
        anchor=ordered.iloc[0]
        groups.append({'campaign':anchor.campaign,'workload_group':group_id,
                       'model_id':anchor.model_id,**{col:anchor[col] for col in GROUP_FEATURES},
                       'anchor_pass':bool(anchor.true_log_latency<=np.log(anchor.latency_slo_ms)
                                          and anchor.true_log_requests_per_second>=
                                          np.log(anchor.target_throughput_rps))})
        targets.append(np.r_[ordered.true_log_latency.to_numpy()[1:]-
                             anchor.anchor_log_latency,
                             ordered.true_log_requests_per_second.to_numpy()[1:]-
                             anchor.anchor_log_requests_per_second])
    return pd.DataFrame(groups),np.stack(targets)


def fit(train_x: pd.DataFrame, train_y: np.ndarray, variant: str):
    rank=VARIANTS[variant]
    basis=None
    labels=train_y
    if rank is not None:
        basis=PCA(n_components=rank,svd_solver='full')
        labels=basis.fit_transform(train_y)
    model=ExtraTreesRegressor(n_estimators=100,min_samples_leaf=2,
                              max_features=1.,random_state=42,n_jobs=2)
    model.fit(train_x[GROUP_FEATURES],labels)
    return model,basis


def predict(model,basis,group_x: pd.DataFrame) -> np.ndarray:
    values=model.predict(group_x[GROUP_FEATURES])
    return basis.inverse_transform(values) if basis is not None else values


def evaluate(predictions: pd.DataFrame) -> dict:
    unprobed=predictions[~predictions.is_anchor].copy()
    unprobed['lerr']=(unprobed.predicted_log_latency-unprobed.true_log_latency).abs()
    unprobed['terr']=(unprobed.predicted_log_requests_per_second-
                      unprobed.true_log_requests_per_second).abs()
    by_model=unprobed.groupby(['campaign','method','model_id']).agg(
        groups=('workload_group','nunique'),latency=('lerr','mean'),
        requests_per_second=('terr','mean')).reset_index()
    metrics={}
    for (campaign,method),rows in by_model.groupby(['campaign','method']):
        metrics[f'{campaign}/{method}']={
            'models':len(rows),'groups':int(rows.groups.sum()),
            'macro_latency_mae_log':float(rows.latency.mean()),
            'macro_requests_per_second_mae_log':float(rows.requests_per_second.mean())}
    main=predictions[predictions.campaign==CAMPAIGNS[0]]
    second=main[(main.replica_count==2)&(main.cpu_core_limit==2)].copy()
    anchor=main[main.is_anchor][['workload_group','method','true_log_latency',
                                 'true_log_requests_per_second',
                                 'latency_slo_ms','target_throughput_rps']].copy()
    anchor['anchor_pass']=(anchor.true_log_latency<=np.log(anchor.latency_slo_ms))&(
        anchor.true_log_requests_per_second>=np.log(anchor.target_throughput_rps))
    second=second.merge(anchor[['workload_group','method','anchor_pass']],
                        on=['workload_group','method'],validate='one_to_one')
    second=second[~second.anchor_pass].copy()
    second['second_pass']=(second.true_log_latency<=np.log(second.latency_slo_ms))&(
        second.true_log_requests_per_second>=np.log(second.target_throughput_rps))
    second['predicted_slack']=np.minimum(
        np.log(second.latency_slo_ms)-second.predicted_log_latency,
        second.predicted_log_requests_per_second-np.log(second.target_throughput_rps))
    rank_results={}
    for method,rows in second.groupby('method'):
        if len(rows)!=326 or int(rows.second_pass.sum())!=108:
            raise ValueError('Unexpected second-probe evaluation population')
        ordered=rows.sort_values(['predicted_slack','workload_group'],ascending=[False,True])
        rank_results[method]={str(k):int(ordered.head(k).second_pass.sum())
                              for k in (25,50,100,150,200,326)}
    return {'prediction':metrics,'second_probe_recoveries':rank_results}


def run():
    source=ROOT/'data/audit_v1/measurements.parquet'
    output=ROOT/'artifacts/response_manifold_v1';output.mkdir(parents=True,exist_ok=True)
    raw=pd.read_parquet(source)
    main=prepare(raw[raw.campaign==CAMPAIGNS[0]])
    external={campaign:prepare(raw[raw.campaign==campaign]) for campaign in CAMPAIGNS[1:]}
    data={campaign:make_groups(frame) for campaign,frame in
          [(CAMPAIGNS[0],main),*external.items()]}
    predictions=[]
    for held_model in sorted(main.model_id.unique()):
        group_train,target_train=data[CAMPAIGNS[0]]
        mask=group_train.model_id!=held_model
        for variant in VARIANTS:
            model,basis=fit(group_train.loc[mask],target_train[mask.to_numpy()],variant)
            for campaign,frame in [(CAMPAIGNS[0],main),*external.items()]:
                groups,_=data[campaign]
                evaluation=groups[groups.model_id==held_model]
                if evaluation.empty:
                    continue
                residual=predict(model,basis,evaluation)
                answer={group_id:vector for group_id,vector in
                        zip(evaluation.workload_group,residual)}
                rows=frame[frame.model_id==held_model].copy()
                indexes={pair:i for i,pair in enumerate(CONFIGS)}
                index=rows.apply(lambda r:indexes[(r.replica_count,r.cpu_core_limit)],axis=1)
                delta=np.array([answer[g][j-1] if j>0 else 0.
                                for g,j in zip(rows.workload_group,index)])
                delta_t=np.array([answer[g][7+j-1] if j>0 else 0.
                                  for g,j in zip(rows.workload_group,index)])
                selected=rows[['campaign','workload_group','execution_id','model_id',
                               'replica_count','cpu_core_limit','latency_slo_ms',
                               'target_throughput_rps','true_log_latency',
                               'true_log_requests_per_second']].copy()
                selected['is_anchor']=index.eq(0).to_numpy()
                selected['method']=variant
                selected['predicted_log_latency']=rows.anchor_log_latency.to_numpy()+delta
                selected['predicted_log_requests_per_second']=(
                    rows.anchor_log_requests_per_second.to_numpy()+delta_t)
                predictions.append(selected)
            print(f'{held_model} {variant}',flush=True)
    predictions=pd.concat(predictions,ignore_index=True)
    predictions.to_parquet(output/'predictions.parquet',index=False)
    result=evaluate(predictions)
    result.update({'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                   'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'outer_split':'leave one model_id out',
                   'response_vector':'seven latency and seven requests/s log changes from anchor',
                   'variants':VARIANTS})
    (output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    run()
