"""Direct candidate-2 feasibility classifier for a fair probe-ranking baseline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier

from train_performance import ROOT

FEATURES=['log_parameter_bytes','log_batch','log_length','concurrency','is_causal',
          'log_anchor_latency','log_anchor_requests_per_second',
          'log_latency_slo','log_target_requests_per_second']


def make_dataset(snapshot):
    raw=pd.read_parquet(snapshot)
    d=raw[(raw.campaign=='khu-hf-local-exhaustive') & raw.eligible & (raw.device=='cuda')].copy()
    anchor=d[(d.replica_count==1)&(d.cpu_core_limit==2)].copy()
    second=d[(d.replica_count==2)&(d.cpu_core_limit==2)].copy()
    assert len(anchor)==len(second)==575
    a=anchor[['workload_group','model_id','batch_size','max_length','request_concurrency',
              'workload_task','latency_slo_ms','target_throughput_rps','latency_p95_ms',
              'examples_per_second','result_json']].copy()
    b=second[['workload_group','latency_p95_ms','examples_per_second']].copy()
    b.columns=['workload_group','second_latency_ms','second_examples_per_second']
    out=a.merge(b,on='workload_group',validate='one_to_one')
    out['anchor_pass']=(out.latency_p95_ms<=out.latency_slo_ms)&(
        out.examples_per_second/out.batch_size>=out.target_throughput_rps)
    out['second_pass']=(out.second_latency_ms<=out.latency_slo_ms)&(
        out.second_examples_per_second/out.batch_size>=out.target_throughput_rps)
    out['log_parameter_bytes']=np.log(out.result_json.map(lambda x: json.loads(x)['model_parameter_bytes']))
    out['log_batch']=np.log(out.batch_size)
    out['log_length']=np.log(out.max_length)
    out['concurrency']=out.request_concurrency
    out['is_causal']=(out.workload_task=='causal_lm_forward').astype(int)
    out['log_anchor_latency']=np.log(out.latency_p95_ms)
    out['log_anchor_requests_per_second']=np.log(out.examples_per_second/out.batch_size)
    out['log_latency_slo']=np.log(out.latency_slo_ms)
    out['log_target_requests_per_second']=np.log(out.target_throughput_rps)
    return out


def main():
    source=ROOT/'data/audit_v1/measurements.parquet'
    d=make_dataset(source)
    rows=[]
    for model in sorted(d.model_id.unique()):
        training=d[(d.model_id!=model)&(~d.anchor_pass)]
        testing=d[(d.model_id==model)&(~d.anchor_pass)].copy()
        clf=ExtraTreesClassifier(n_estimators=200,min_samples_leaf=2,random_state=42,n_jobs=2)
        clf.fit(training[FEATURES],training.second_pass.astype(int))
        testing['predicted_feasible_probability']=clf.predict_proba(testing[FEATURES])[:,list(clf.classes_).index(1)]
        rows.append(testing[['model_id','workload_group','anchor_pass','second_pass','predicted_feasible_probability']])
    predictions=pd.concat(rows,ignore_index=True)
    predictions=predictions.sort_values(['predicted_feasible_probability','workload_group'],ascending=[False,True])
    curves={}
    for budget in [25,50,100,150,200,326]:
        selected=predictions.head(budget)
        curves[str(budget)]={'verified_recoveries':int(selected.second_pass.sum()),
                             'recovery_fraction_of_108':float(selected.second_pass.sum()/108)}
    output=ROOT/'artifacts/probe_classifier_v1';output.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(output/'predictions.csv',index=False)
    result={'method':'leave-one-model-out direct second-probe feasibility ExtraTreesClassifier',
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'features':FEATURES,'anchor_fail_groups':len(predictions),
            'feasible_second_probe_groups':int(predictions.second_pass.sum()),'curves':curves}
    (output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
