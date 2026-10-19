"""Verify saved GPU experiment claims against frozen measurements and choices."""
import hashlib
import json

import pandas as pd

from train_performance import ROOT


def verify():
    source=ROOT/'data/audit_v1/measurements.parquet'
    output=ROOT/'artifacts/gpu_policy_v1'
    metrics=json.loads((output/'metrics.json').read_text())
    assert metrics['source_snapshot_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert metrics['script_sha256']==hashlib.sha256((ROOT/'scripts/evaluate_gpu_policy.py').read_bytes()).hexdigest()
    assert metrics['model_sha256']==hashlib.sha256((output/'gpu_performance_model.joblib').read_bytes()).hexdigest()
    predictions=pd.read_parquet(output/'predictions.parquet')
    assert len(predictions)==4600
    assert predictions.groupby('workload_group').split.nunique().max()==1
    assert predictions.groupby('workload_group').size().eq(8).all()
    assert set(predictions.groupby('workload_group').split.first().value_counts())=={297,99,89,90}
    assert ((predictions.examples_per_second/predictions.batch_size - predictions.measured_requests_per_second).abs()<1e-10).all()
    checked={}
    for label,path in [('test',output/'choices_test.csv')]+[(name,output/f'choices_{name}.csv') for name in metrics['external_stress_tests']]:
        choices=pd.read_csv(path)
        assert choices.groupby('workload_group').policy.nunique().eq(7).all()
        assert len(choices)==7*choices.workload_group.nunique()
        for row in choices.itertuples():
            if not row.selected:
                assert pd.isna(row.execution_id)
                continue
            assert bool(row.success)==bool(row.true_latency_ms <= row.latency_slo_ms and
                                          row.true_requests_per_second >= row.throughput_slo_rps)
            assert int(row.violation)==int(not row.success)
        report=metrics['test_policies'] if label=='test' else metrics['external_stress_tests'][label]['policies']
        for policy,part in choices.groupby('policy'):
            assert int(part.selected.sum())==report[policy]['selected']
            assert int(part.violation.sum())==report[policy]['violations']
        checked[label]={'groups':int(choices.workload_group.nunique()),'policy_rows':len(choices)}
    result={'status':'verified','snapshot_sha256':metrics['source_snapshot_sha256'],
            'model_sha256':metrics['model_sha256'],'split_groups_disjoint':True,
            'measured_throughput_unit_conversion_checked':True,'policy_outcomes_recomputed':checked}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    verify()
