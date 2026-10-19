"""Replay frozen Azure predictors in an explicitly hypothetical token-capacity system."""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from demand_capacity_policy import Config, replay, summarize, feedback_correct


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def prepare(root, out):
    sys.path.insert(0, str(root / 'scripts'))
    import evaluate_azure_code_input_demand as coding
    import evaluate_azure_lmm_demand_transfer as lmm
    manifests = [json.loads((root / f'artifacts/{name}/manifest.json').read_text())
                 for name in ('azure_code_input_demand_v1', 'azure_lmm_demand_transfer_v1')]
    cm, lm = manifests
    files = {
        root / 'data/external/azure_llm_2024/AzureLLMInferenceTrace_code_1week.csv': cm['source_sha256'],
        root / 'data/external/azure_lmm_2025/AzureLMMInferenceTrace_multimodal.csv.gz': lm['source_sha256'],
        root / 'artifacts/azure_code_input_demand_v1/relative_q90_model.joblib': cm['model_sha256'],
        root / 'artifacts/azure_lmm_demand_transfer_v1/lmm_token_only_model.joblib': lm['token_model_sha256'],
        root / 'artifacts/azure_lmm_demand_transfer_v1/lmm_modality_model.joblib': lm['modality_model_sha256'],
        root / 'scripts/evaluate_azure_code_input_demand.py': cm['script_sha256'],
        root / 'scripts/evaluate_azure_lmm_demand_transfer.py': lm['script_sha256'],
    }
    if sklearn.__version__ != '1.6.1' or np.__version__ != '2.0.2':
        raise ValueError('use pinned sklearn 1.6.1 and numpy 2.0.2')
    for path, expected in files.items():
        if sha(path) != expected: raise ValueError(f'changed input: {path}')
    protocol = json.loads((Path(__file__).parents[1] / 'reports/demand_capacity_protocol_v1.json').read_text())
    if protocol['status'] != 'EXPLORATORY_SIMULATION' or protocol['capacity_factors'] != [.5,1.,2.] or protocol['startup_bins'] != [1,2,6]:
        raise ValueError('protocol mismatch')
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'freeze.json', {
        'protocol': protocol, 'protocol_sha256': sha(Path(__file__).parents[1] / 'reports/demand_capacity_protocol_v1.json'),
        'inputs': {str(k): v for k,v in files.items()},
        'code': {p.name: sha(p) for p in (Path(__file__), Path(__file__).with_name('demand_capacity_policy.py'), Path(__file__).parents[1] / 'tests/test_demand_capacity_policy.py')},
        'python': sys.version, 'numpy': np.__version__, 'sklearn': sklearn.__version__,
        'frozen_before_aggregation_and_policy_scores': True,
        'historical_forecast_evaluation_periods_already_exposed': True,
    })
    return coding, lmm, files


def datasets(root, coding, lmm):
    bundle = joblib.load(root / 'artifacts/azure_code_input_demand_v1/relative_q90_model.joblib')
    y, counts, rows = coding.aggregate_source(root / 'data/external/azure_llm_2024/AzureLLMInferenceTrace_code_1week.csv')
    if rows != coding.EXPECTED_ROWS: raise ValueError('Coding row count')
    frame = coding.design_from_aggregates(y)
    predictions = coding.predict_from_bundle(bundle, frame)
    coding_methods = {'model': predictions['relative_q90_hgb']}
    for key in coding.BASELINES: coding_methods[key] = predictions[key]
    coding_methods['persistence'] = np.r_[np.nan, y[:-1]]
    coding_methods['past_hour_mean'] = frame['scale'].to_numpy(float)
    yield 'coding', frame, coding_methods, int(rows)
    y, images, counts, audit = lmm.aggregate_source(root / 'data/external/azure_lmm_2025/AzureLMMInferenceTrace_multimodal.csv.gz')
    if audit['request_rows'] != 1_000_000: raise ValueError('LMM row count')
    frame = lmm.build_design(y, images, counts)
    scale = frame['scale'].to_numpy(float)
    train = frame.iloc[coding.TRAIN]
    weights = train['scale'].to_numpy(float)
    global_q = coding.weighted_quantile(train['ratio'].to_numpy(float), weights)
    residual = (train['ratio'] - train['previous_day_ratio']).to_numpy(float)
    ok = np.isfinite(residual)
    residual_q = coding.weighted_quantile(residual[ok], weights[ok])
    past = frame['previous_day_ratio'].to_numpy(float)
    has = np.isfinite(past)
    cp = coding.predict_from_bundle(bundle, frame)
    token = joblib.load(root / 'artifacts/azure_lmm_demand_transfer_v1/lmm_token_only_model.joblib')
    modality = joblib.load(root / 'artifacts/azure_lmm_demand_transfer_v1/lmm_modality_model.joblib')
    methods = {
        'model': np.maximum(0., modality.predict(frame[list(lmm.TOKEN_FEATURES + lmm.MODALITY_FEATURES)].to_numpy(float))) * scale,
        'token_only_model': np.maximum(0., token.predict(frame[list(lmm.TOKEN_FEATURES)].to_numpy(float))) * scale,
        'coding_transferred_model': cp['relative_q90_hgb'],
        'train_weighted_ratio_q90': global_q * scale,
        'past_hour_empirical_q90': frame['past_hour_empirical_q90'].to_numpy(float),
        'previous_day_ratio': np.maximum(0., np.where(has,past,global_q)) * scale,
        'previous_day_ratio_plus_train_residual_q90': np.maximum(0.,np.where(has,past+residual_q,global_q))*scale,
        'persistence': np.r_[np.nan,y[:-1]], 'past_hour_mean': scale,
    }
    yield 'lmm', frame, methods, audit['request_rows']


def evaluate(root, out):
    coding, lmm, original_files = prepare(root, out)
    summaries, diagnostics, comparison = [], {}, {}
    with gzip.open(out / 'decision_replay.csv.gz', 'wt', newline='') as stream:
        writer = None
        for name, frame, methods, request_rows in datasets(root,coding,lmm):
            y, scale = frame['y'].to_numpy(float), frame['scale'].to_numpy(float)
            # Select the non-ML comparison from validation forecast scores only.
            val = slice(864,1152)
            candidates = ('train_weighted_ratio_q90','past_hour_empirical_q90', 'previous_day_ratio','previous_day_ratio_plus_train_residual_q90')
            chosen = min(candidates, key=lambda k: float(coding.pinball(y[val],methods[k][val]).mean()))
            selected_baseline = chosen
            for key in ('model', chosen):
                methods[key+'_feedback'] = feedback_correct(y,methods[key],scale,864)
            comparison[name] = {'baseline': chosen+'_feedback', 'candidate': 'model_feedback'}
            diagnostics[name] = {'requests':request_rows,'validation_selected_baseline':selected_baseline,'forecast_metrics':{}}
            train_median = float(np.median(y[12:864]))
            train_q90 = float(np.quantile(y[12:864],.9))
            for key,p in methods.items():
                if not np.isfinite(p[864:]).all() or (p[864:] < 0).any(): raise ValueError('unsupported forecast grid')
                diagnostics[name]['forecast_metrics'][key] = {
                    'pinball': float(coding.pinball(y[1152:],p[1152:]).mean()),
                    'coverage': float(np.mean(y[1152:] <= p[1152:])),
                    'daily_coverage': [float(np.mean(y[t:t+288] <= p[t:t+288])) for t in (1152,1440,1728)]}
            arrays = pd.DataFrame({'y':y,'scale':scale,**methods})
            arrays.to_parquet(out / (name+'_aggregate_and_forecasts.parquet'),index=False)
            for factor in (.5,1.,2.):
                capacity = train_median / 2 * factor
                initial = min(8,max(1,math.ceil(train_median/capacity)))
                for delay in (1,2,6):
                    cfg = Config(capacity,startup_bins=delay)
                    for method in tuple(methods) + ('fixed_train_q90',):
                        fixed = method == 'fixed_train_q90'
                        p = np.full(len(y),train_q90) if fixed else methods[method]
                        slots = min(8,max(1,math.ceil(train_q90/capacity))) if fixed else initial
                        all_rows = replay(y[864:],p[864:],cfg,slots,fixed=fixed)
                        scored = all_rows[288:]
                        metrics = summarize(scored)
                        row = {'dataset':name,'method':method,'capacity_factor':factor,'startup_bins':delay,
                               'capacity_tokens_per_slot_bin':capacity,**metrics}
                        summaries.append(row)
                        for r in scored:
                            trace = {'dataset':name,'method':method,'capacity_factor':factor,'startup_bins':delay,
                                     'absolute_bin':r['bin']+864, **r}
                            if writer is None:
                                writer = csv.DictWriter(stream,fieldnames=list(trace));writer.writeheader()
                            writer.writerow(trace)
    primary = {}
    for name, pair in comparison.items():
        rows = {r['method']:r for r in summaries if r['dataset']==name and r['capacity_factor']==1 and r['startup_bins']==1}
        a,b = rows[pair['candidate']],rows[pair['baseline']]
        # Deliberately require improvement in both independent proxy objectives.
        gate = (a['billed_slot_minutes'] < b['billed_slot_minutes']
                and a['shortfall_fraction'] <= b['shortfall_fraction']
                and a['backlog_area_over_total_tokens'] <= b['backlog_area_over_total_tokens']
                and a['final_backlog_tokens'] <= b['final_backlog_tokens']
                and a['direction_reversals'] <= b['direction_reversals'])
        primary[name] = {**pair,'candidate_metrics':a,'baseline_metrics':b,
                         'gate':'EXPLORATORY_PROXY_PASS' if gate else 'NO_GO',
                         'operational_adoption':False}
    for path,expected in original_files.items():
        if sha(path)!=expected: raise ValueError('original input changed during evaluation')
    write_json(out/'results.json',{'status':'EXPLORATORY_SIMULATION','primary':primary,'diagnostics':diagnostics,'scenarios':summaries,
                                 'actual_vm_or_gpu_capacity_measured':False,'actual_user_slo_measured':False,
                                 'actual_scale_actions_executed':False,'original_inputs_unchanged':True})
    pd.DataFrame(summaries).to_csv(out/'scenario_summary.csv',index=False)
    report = ['AI-MCMP: 수요 예측을 이용한 가상 용량 판단 실험', '',
              '기존 모델을 다시 학습하지 않고 저장된 Coding/LMM 예측기를 판단 정책에 연결했다.',
              '이미 평가된 기간을 사용한 탐색 실험이며 실제 VM/GPU 용량이나 사용자 SLO의 검증이 아니다.',
              '용량 단위: 가상 slot당 5분 입력 토큰. 서버/GPU/VM을 뜻하지 않는다.',
              '외생 수요, 일정 처리 용량, 준비 5/10/30분, 축소 즉시 적용, 무손실 유체 대기열을 가정했다.',
              '준비 중 slot에도 비용 대리값을 부과했다. 과거 대기량만 판단에 사용한다.',
              '학습 중앙값/2를 용량으로 두고 0.5/1/2배를 비교했다. 실제 가격과 무관하다.',
              '정책: 복제 +1/-1, 10분 cooldown, 세 연속 저수요 창 후 축소, 1~8slot.',
              '예측은 다음 5분이며 긴 준비 지연 동안의 부하를 미리 예측하는 모델은 아니다.',
              '직전 최대 288개 완료 창(24시간)의 양수 q90 잔차 보정을 ML과 검증 선택 기준선에 동일하게 적용했다.',
              '정답 창 종료 즉시 피드백을 사용할 수 있다고 가정했다. 90% 피복 보장은 없다.',
              '검증 1일로 단순 비교기를 선택하고 상태를 warm-up, 이후 3일은 연속 재생했다.',
              'Philly/Alibaba의 기계 사용률을 Azure 요청과 같은 앱의 관측처럼 결합하지 않았다.', '']
    for name,pair in primary.items():
        a,b=pair['candidate_metrics'],pair['baseline_metrics']
        d=diagnostics[name]['forecast_metrics']
        report.extend([name.upper()+': '+pair['gate'], '비교 기준: '+pair['baseline'],
                       f"가상 slot-minutes 기준선→후보: {b['billed_slot_minutes']}→{a['billed_slot_minutes']}",
                       f"입력량 대비 즉시 용량 부족: {b['shortfall_fraction']:.6f}→{a['shortfall_fraction']:.6f}",
                       f"누적 대기량/총 입력량: {b['backlog_area_over_total_tokens']:.6f}→{a['backlog_area_over_total_tokens']:.6f}",
                       f"증설/축소 방향 전환 횟수: {b['direction_reversals']}→{a['direction_reversals']}",
                       '원 모델 일별 피복률: '+str(d['model']['daily_coverage']),
                       '피드백 보정 일별 피복률: '+str(d['model_feedback']['daily_coverage']), ''])
    report.extend(['판정 조건은 주 시나리오에서 비용 대리값 감소와 부족량·대기량·최종 대기량·방향 전환 비악화를 동시에 요구한다.',
                   '이 실험의 PASS는 가정 아래 대리 지표의 탐색 개선만 뜻한다. 운영 정책 채택 판정은 항상 false다.',
                   '9개 용량/준비 지연 시나리오를 모두 보존했고 유리한 시나리오만 골라 대표 성과로 쓰지 않는다.',
                   '다음 단계: 실제 용량/가격/준비시간 측정과 새 기간 시험. INNO/ETRI 사건 계약에 사용자 결과를 연결해야 한다.'])
    (out/'findings.txt').write_text('\n'.join(report)+'\n',encoding='utf8')
    write_json(out/'manifest.json',{p.name:sha(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps({'primary':primary,'scenario_count':len(summaries)},ensure_ascii=False))


if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--research-root',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    evaluate(args.research_root,args.output)
