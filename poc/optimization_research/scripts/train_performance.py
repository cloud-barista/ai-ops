"""First offline experiment: predict measured batch latency and examples/sec."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ['latency_p95_ms', 'examples_per_second']
CATEGORICAL = ['model_id', 'model_revision', 'device']
NUMERIC = ['batch_size', 'max_length', 'request_concurrency', 'cpu_threads',
           'cpu_core_limit', 'replica_count', 'repeats']
FEATURES = CATEGORICAL + NUMERIC
SUPPORT_COMBINATION = ['model_id', 'model_revision', 'host', 'device', 'device_name', 'replica_count']
PRIMARY = 'khu-hf-local-exhaustive'


def assign_splits(frame, seed=42):
    """Stratify by model; keep all resource alternatives of a workload together."""
    rng = np.random.default_rng(seed)
    assignment = {}
    for _, part in frame.groupby('model_id', sort=True):
        groups = np.array(sorted(part.workload_group.unique()))
        if len(groups) < 10:
            raise ValueError('Too few independent groups for three-way evaluation')
        rng.shuffle(groups)
        train_end, val_end = int(len(groups)*0.7), int(len(groups)*0.85)
        for split, keys in [('train', groups[:train_end]),
                            ('validation', groups[train_end:val_end]),
                            ('test', groups[val_end:])]:
            assignment.update({key: split for key in keys})
    return frame.workload_group.map(assignment)


def group_mean(values, groups):
    return pd.Series(values).groupby(np.asarray(groups)).mean()


def metrics(truth, predicted, groups):
    result = {}
    for i, target in enumerate(TARGETS):
        y, p = truth[:, i], predicted[:, i]
        relative = np.abs(p-y)/y
        log_error = np.abs(np.log(p)-np.log(y))
        result[target] = {
            'mae': float(np.abs(p-y).mean()),
            'median_absolute_percentage_error': float(np.median(relative)*100),
            'p90_absolute_percentage_error': float(np.quantile(relative, .9)*100),
            'group_mean_absolute_log_error': float(group_mean(log_error, groups).mean()),
        }
    result['selection_score'] = float(np.mean([
        result[t]['group_mean_absolute_log_error'] for t in TARGETS]))
    return result


def baseline_fit(train):
    values = train.copy()
    values[TARGETS] = np.log(values[TARGETS])
    return values.groupby(['model_id', 'device'])[TARGETS].median(), values[TARGETS].median().to_numpy()


def baseline_predict(frame, fitted):
    lookup, fallback = fitted
    return np.exp(np.array([lookup.loc[(r.model_id, r.device)].to_numpy()
                           if (r.model_id, r.device) in lookup.index else fallback
                           for r in frame.itertuples()]))


def make_model(kind, seed):
    preprocessor = ColumnTransformer([
        ('categorical', OneHotEncoder(handle_unknown='ignore', sparse_output=False), CATEGORICAL),
        ('numeric', 'passthrough', NUMERIC)])
    cls = RandomForestRegressor if kind == 'random_forest' else ExtraTreesRegressor
    return Pipeline([('features', preprocessor), ('regressor', cls(
        n_estimators=200, min_samples_leaf=2, max_features=1.0,
        random_state=seed, n_jobs=2))])


def support_mask(train, candidate):
    mask = pd.Series(True, index=candidate.index)
    # Scope includes hardware and host even though they are constants in training.
    for col in CATEGORICAL + ['host', 'device_name']:
        mask &= candidate[col].isin(train[col].unique())
    for col in NUMERIC:
        mask &= candidate[col].between(train[col].min(), train[col].max())
    combinations = set(map(tuple, train[SUPPORT_COMBINATION].to_numpy()))
    mask &= candidate[SUPPORT_COMBINATION].apply(lambda row: tuple(row) in combinations, axis=1)
    return mask


def bootstrap_improvement(frame, model_pred, baseline_pred, seed):
    truth = frame[TARGETS].to_numpy()
    model_loss = group_mean(np.abs(np.log(model_pred)-np.log(truth)).mean(axis=1), frame.workload_group)
    baseline_loss = group_mean(np.abs(np.log(baseline_pred)-np.log(truth)).mean(axis=1), frame.workload_group)
    delta = (baseline_loss-model_loss).to_numpy()
    rng = np.random.default_rng(seed)
    samples = rng.choice(delta, size=(2000, len(delta)), replace=True).mean(axis=1)
    return {'baseline_minus_model': float(delta.mean()),
            'group_bootstrap_95_percent_ci': np.quantile(samples, [.025, .975]).tolist(),
            'groups': len(delta), 'interpretation': 'positive favors model; conditional on this trained model'}


def train(snapshot, output, seed):
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    eligible = raw[raw.eligible].copy()
    primary = eligible[eligible.campaign == PRIMARY].copy()
    if primary[FEATURES+TARGETS].isna().any().any():
        raise ValueError('Missing feature/target: define handling before fitting')
    primary['split'] = assign_splits(primary, seed)
    splits = {s: primary[primary.split == s].copy() for s in ['train', 'validation', 'test']}
    for a, b in [('train', 'validation'), ('train', 'test'), ('validation', 'test')]:
        assert not set(splits[a].workload_group) & set(splits[b].workload_group)
    primary[['source_db', 'host', 'execution_id', 'workload_group', 'condition_group', 'split']].to_csv(
        output/'split_manifest.csv', index=False)
    # Equal total weight for every workload, despite unequal candidate counts.
    weights = 1/splits['train'].groupby('workload_group').workload_group.transform('size')
    weights = weights / weights.mean()
    baseline = baseline_fit(splits['train'])
    validation = {}
    models = {}
    val = splits['validation']
    validation['model_device_median'] = metrics(val[TARGETS].to_numpy(), baseline_predict(val, baseline), val.workload_group)
    for name in ['random_forest', 'extra_trees']:
        model = make_model(name, seed)
        model.fit(splits['train'][FEATURES], np.log(splits['train'][TARGETS]), regressor__sample_weight=weights)
        models[name] = model
        pred = np.exp(model.predict(val[FEATURES]))
        validation[name] = metrics(val[TARGETS].to_numpy(), pred, val.workload_group)
        print(f'validation {name}: {validation[name]["selection_score"]:.4f}', flush=True)
    chosen = min(validation, key=lambda k: validation[k]['selection_score'])
    def predict(frame):
        return baseline_predict(frame, baseline) if chosen == 'model_device_median' else np.exp(models[chosen].predict(frame[FEATURES]))
    # No retraining or test-driven model selection: frozen train-only model.
    reports, predictions = {}, []
    def evaluate(name, frame):
        if len(frame) == 0:
            reports[name] = {'rows': 0}
            return
        pred, base = predict(frame), baseline_predict(frame, baseline)
        reports[name] = {'rows': len(frame), 'groups': int(frame.workload_group.nunique()),
                         'model': metrics(frame[TARGETS].to_numpy(), pred, frame.workload_group),
                         'baseline': metrics(frame[TARGETS].to_numpy(), base, frame.workload_group),
                         'improvement': bootstrap_improvement(frame, pred, base, seed)}
        reports[name]['by_device'] = {}
        for device in sorted(frame.device.unique()):
            subset = (frame.device == device).to_numpy()
            reports[name]['by_device'][device] = {
                'rows': int(subset.sum()),
                'model': metrics(frame.loc[subset, TARGETS].to_numpy(), pred[subset], frame.loc[subset, 'workload_group'])}
        out = frame[['host', 'execution_id', 'campaign', 'model_id', 'device', 'workload_group']+TARGETS].copy()
        out['evaluation'] = name
        for i, target in enumerate(TARGETS):
            out['predicted_'+target] = pred[:, i]
            out['baseline_'+target] = base[:, i]
        predictions.append(out)
    evaluate('test', splits['test'])
    external_coverage = {}
    for campaign in ['khu-hf-local-novel-matrix', 'khu-hf-local-space-filling']:
        external = eligible[eligible.campaign == campaign].copy()
        independent = ~external.workload_group.isin(primary.workload_group)
        supported = support_mask(splits['train'], external)
        external_coverage[campaign] = {'eligible_rows': len(external),
            'overlapping_rows_excluded': int((~independent).sum()),
            'outside_training_support_rows': int((~supported).sum()),
            'evaluated_rows': int((independent & supported).sum())}
        evaluate(campaign, external[independent & supported])
    pd.concat(predictions).to_parquet(output/'predictions.parquet', index=False)
    bundle = {'model': models.get(chosen), 'baseline': baseline, 'selected': chosen,
              'features': FEATURES, 'targets': TARGETS,
              'categorical_support': {k: sorted(splits['train'][k].unique().tolist()) for k in CATEGORICAL+['host','device_name']},
              'numeric_support': {k: [float(splits['train'][k].min()), float(splits['train'][k].max())] for k in NUMERIC}}
    bundle['support_combination_columns'] = SUPPORT_COMBINATION
    bundle['support_combinations'] = sorted(set(map(tuple, splits['train'][SUPPORT_COMBINATION].to_numpy())))
    joblib.dump(bundle, output/'performance_model.joblib')
    result = {'selected_model': chosen, 'seed': seed,
              'task': 'same-host known-model benchmark performance prediction',
              'feature_columns': FEATURES, 'target_columns': TARGETS,
              'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
              'model_sha256': hashlib.sha256((output/'performance_model.joblib').read_bytes()).hexdigest(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'versions': {'python':platform.python_version(), 'sklearn':sklearn.__version__, 'numpy':np.__version__, 'pandas':pd.__version__},
              'split_counts': {k: {'rows':len(v),'groups':int(v.workload_group.nunique())} for k,v in splits.items()},
              'validation':validation, 'evaluations':reports, 'external_coverage':external_coverage,
              'promotion': 'offline research only; no deployment/scaling action authorized by model'}
    (output/'metrics.json').write_text(json.dumps(result, indent=2)+'\n')
    lines = ['# 첫 성능 예측 실험', '', f'- 선택 모델: **{chosen}** (validation만 사용)',
             '- 입력: 모델/revision, CPU/GPU 종류, batch/길이/동시성, CPU 제한/스레드, replica 수, 반복 수.',
             '- 타깃: 배치 p95 지연(ms), 처리 샘플/초. 로그 공간에서 학습하며 workload 그룹별 동일 가중.',
             '- 주 데이터: local-exhaustive. 모델별 workload 그룹 70/15/15 분할. 동일 조건의 자원 후보가 분할을 넘지 않는다.',
             '- 비교 기준: train의 모델·device별 로그 타깃 중앙값.', '',
             '| 평가셋 | 행/그룹 | 기준선 로그오차 | 모델 로그오차 | 개선량 95% 구간 |',
             '| --- | ---: | ---: | ---: | --- |']
    for key, value in reports.items():
        if not value['rows']:
            continue
        lo, hi = value['improvement']['group_bootstrap_95_percent_ci']
        lines.append(f'| {key} | {value["rows"]}/{value["groups"]} | '
                     f'{value["baseline"]["selection_score"]:.4f} | {value["model"]["selection_score"]:.4f} | [{lo:.4f}, {hi:.4f}] |')
    lines += ['', '로그오차는 두 타깃의 그룹 평균 절대 로그오차 평균이며 작을수록 좋다. 양의 개선량은 기준선 대비 개선을 뜻한다.',
        '', '## 범위와 한계', '',
        '- 같은 호스트·이미 본 모델의 새로운 입력 조건에 대한 실험이다. 시간 외삽, 새 응용, 새 GPU/호스트의 일반화는 검증하지 않았다.',
        '- external 평가는 겹치는 workload와 학습 범위 밖 입력을 제외한다. 제외 건수는 metrics.json에 기록했다.',
        '- replica 처리량 합산과 worst-replica p95를 예측한다. 실제 분산 서비스의 end-to-end 처리량/지연이 아니다.',
        '- 적은 반복으로 계산한 p95는 변동성이 크다. 모델 오차에는 측정 잡음이 포함된다.',
        '- bootstrap 구간은 test workload 표본 변동을 나타내며 재학습 seed나 장비 변화의 불확실성을 포함하지 않는다.',
        '- 실행 후 사용률·메모리·측정시간·보상·SLO 판정은 입력에서 제외했다. 모델 ID와 자원/부하 설정만 사용한다.',
        '- 실제 가격·VM 배포·변경 이력은 학습하지 않았다. 예측 결과로 자동 배포/스케일링을 실행하지 않는다.', '']
    (output/'experiment_report.md').write_text('\n'.join(lines))
    print(json.dumps({'selected':chosen,'test':reports['test']}, indent=2), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=ROOT/'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/performance_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    train(args.snapshot, args.output, args.seed)
